"""Exercise real Gradio callbacks without requiring a browser or ML assets."""
import asyncio
import base64
import json
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app, parser, resolve_app_paths
import gradio as gr
from annotation.state import (new_state, set_verified_content,
                              refresh_bbox_validation, initialize_alignment)
from annotation.bbox import add_bbox
from annotation.status import confirm_status
from annotation.io import atomic_write
from annotation.text_extraction import content_fields
from annotation.workflow import Workflow
from ui.presentation import SECTION_LABELS, header
from ui.editor import snapshot, source_text
from PIL import Image


class GradioCallbacks(unittest.TestCase):
    def test_reopen_image_after_back_and_switch_preserves_image_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            image_a=root/'1.png';image_b=root/'2.png'
            Image.new('RGB',(100,100),'white').save(image_a)
            Image.new('RGB',(100,100),'white').save(image_b)
            source=root/'source.json'
            atomic_write(source,[
                {'noi_dung':[{'ky_hieu':'1','chuyen_muc':[
                    {'tieu_de':'Nguyên văn chữ Hán Nôm','van_ban':'甲'}]}]},
                {'noi_dung':[{'ky_hieu':'2','chuyen_muc':[
                    {'tieu_de':'Nguyên văn chữ Hán Nôm','van_ban':'乙'}]}]},
            ])
            options=parser().parse_args([
                '--image-dir',str(root),'--source-json',str(source),
                '--output-dir',str(root/'out'),'--skip-detection'])
            app=create_app(options)
            functions=[f.fn for f in app.fns.values() if f.fn]
            open_image=next(f for f in functions if f.__name__=='open_image')
            back_action=next(f for f in functions
                             if f.__name__=='<lambda>' and f.__defaults__==('back',))

            ctx=dict(active=new_state(),drafts={})
            for path in (image_a,image_a,image_b,image_b,image_a):
                opened=open_image(ctx,str(path))
                ctx=opened[0]
                self.assertEqual(ctx['active']['image_path'],str(path))
                self.assertEqual(ctx['active']['current_step'],2)
                self.assertNotIn(str(path),ctx['drafts'])
                self.assertEqual(ctx['active']['draft_content']['noi_dung'][0]['chuyen_muc'][0]['van_ban'],
                                 '甲' if path==image_a else '乙')
                ctx=back_action(ctx)[0]
                self.assertEqual(ctx['active']['current_step'],1)

    def test_fonts_preload_pmingliu(self):
        from ui.fonts import FONT_CSS, FONT_FILES, DEFAULT_FONT_STACK
        from app import FONT_PRELOAD_JS
        for family in ('PMingLiU', 'PMingLiU-ExtB'):
            self.assertTrue(FONT_FILES[family].is_file())
            self.assertIn(f'font-family: "{family}"', FONT_CSS)
            self.assertIn(f'"{family}"', DEFAULT_FONT_STACK)
            self.assertIn(family, FONT_PRELOAD_JS)

    def test_repeated_sort_results_have_distinct_update_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();image=root/'1.png'
            Image.new('RGB',(100,100),'white').save(image)
            source=root/'source.json'
            atomic_write(source,[{'noi_dung':[{'ky_hieu':'1','chuyen_muc':[
                {'tieu_de':'Nguyên văn chữ Hán Nôm','van_ban':'甲乙'}]}]}])
            options=parser().parse_args([
                '--image-dir',str(root),'--source-json',str(source),
                '--output-dir',str(root/'out'),'--skip-detection'])
            app=create_app(options)
            functions=[f.fn for f in app.fns.values() if f.fn]
            open_image=next(f for f in functions if f.__name__=='open_image')
            on_action=next(f for f in functions if f.__name__=='on_action')
            ctx=open_image(dict(active=new_state(),drafts={}),str(image))[0]
            state=ctx['active']
            state['current_step']=3
            state['annotation_text']='甲乙丙丁'
            state['workflow']['content_verified']=True
            state['workflow']['bbox_valid']=False
            state['source_mismatch']={
                'source_text':state['annotation_text'],
                'source_character_count':4,
                'bounding_box_count':3,
                'issue_type':'extra_text',
                'note':'',
            }
            state['regions']={
                'left':dict(bbox=[10,0,20,10],status='intact',unknown=False,order=None),
                'middle':dict(bbox=[40,0,50,10],status='intact',unknown=False,order=None),
                'right':dict(bbox=[70,0,80,10],status='intact',unknown=False,order=None),
            }
            boxes={uid:dict(box) for uid,box in state['regions'].items()}
            sort_payload={
                'boxes':{uid:boxes[uid]['bbox'] for uid in ('left','middle')},
                'allBoxes':boxes,
                'selectedIds':['left','middle'],
            }
            confirmation=state.pop('source_mismatch')
            rejected_unconfirmed=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc','payload':dict(sort_payload,sortRequestId='unconfirmed')}))
            self.assertEqual(rejected_unconfirmed[8]['value']['calcSortedBoxIds'],[])
            state['source_mismatch']=confirmation
            responses=[]
            first=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc',
                'payload':dict(sort_payload,sortRequestId='sort-1')}))
            responses.append(first[8]['value'])
            for index,uid in enumerate(first[8]['value']['calcSortedBoxIds'],1):
                boxes[uid]['order']=index
            # Clear Order changes only draft order metadata.
            for box in boxes.values():
                box['order']=None
            second=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc',
                'payload':dict(sort_payload,sortRequestId='sort-2')}))
            responses.append(second[8]['value'])
            self.assertEqual(responses[0]['calcSortedBoxIds'],['middle','left'])
            self.assertEqual(responses[1]['calcSortedBoxIds'],['middle','left'])
            self.assertEqual([value['sortRequestId'] for value in responses],
                             ['sort-1','sort-2'])
            self.assertNotEqual(responses[0],responses[1])
            same_count_boxes=dict(boxes)
            same_count_boxes['replacement']=same_count_boxes.pop('right')
            same_count=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc',
                'payload':dict(sort_payload,allBoxes=same_count_boxes,
                               sortRequestId='sort-same-count')}))
            self.assertEqual(same_count[8]['value']['calcSortedBoxIds'],
                             ['middle','left'])
            changed_membership=dict(
                sort_payload,allBoxes={key:boxes[key] for key in ('left','middle')},
                sortRequestId='sort-changed-membership')
            changed=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc','payload':changed_membership}))
            self.assertEqual(changed[8]['value']['sortRequestId'],
                             'sort-changed-membership')
            self.assertEqual(changed[8]['value']['calcSortedBoxIds'],[])
            invalid_payload=dict(sort_payload,boxes={},sortRequestId='sort-rejected')
            rejected=on_action(ctx,gr.EventData(None,{
                'action':'sort_boxes_calc','payload':invalid_payload}))
            self.assertEqual(rejected[8]['value']['sortRequestId'],'sort-rejected')
            self.assertEqual(rejected[8]['value']['calcSortedBoxIds'],[])

    def test_miss_box_is_derived_unknown_and_rendered_yellow(self):
        state=new_state();state.update(image='12305.png',image_size=[100,100],
                                      image_url='image.jpg',current_step=4)
        set_verified_content(state,{},'永寺')
        for x in (0,20,40):add_bbox(state,[x,0,x+10,10])
        state['source_mismatch']={
            'source_text':state['annotation_text'],'source_character_count':2,
            'bounding_box_count':3,'issue_type':'missing_text','note':''}
        refresh_bbox_validation(state);confirm_status(state);initialize_alignment(state)
        missing_id=next(key for key,value in state['annotations'].items() if value=='MISS')
        self.assertEqual(state['bounding_boxes'][missing_id]['status'],'intact')
        reading_markup=snapshot(state)['markup']
        self.assertIn('data-missing="1"',reading_markup)
        self.assertIn('<g data-miss-mark="1"',reading_markup)
        state['current_step']=5
        markup=snapshot(state)['markup']
        self.assertIn(f'<title>{missing_id} MISS · intact</title>',markup)
        self.assertIn('data-missing="1"',markup)
        self.assertIn('<span class="missing">MISS content</span>',markup)
        state['current_step']=7
        review=snapshot(state)['markup']
        self.assertIn('<span class="eyebrow">REVIEW & VERIFICATION</span>',review)
        self.assertNotIn('<span class="eyebrow">SOURCE MISMATCH</span>',review)

    def test_canvas_script_keeps_selection_and_geometry_local_until_next(self):
        script=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertNotIn("send('select'",script)
        self.assertNotIn("send('commit_boxes'",script)
        self.assertNotIn("send('status'",script)
        self.assertIn('statuses: Object.fromEntries',script)
        self.assertIn('boxes: Object.fromEntries',script)
        self.assertIn("kind: 'marquee'",script)
        self.assertIn('selectedIds = new Set()',script)
        self.assertIn('const showResizeHandles = selectedIds.size === 1',script)
        self.assertIn("group.classList.toggle('active-region', active && showResizeHandles)",script)
        self.assertIn("event.target.closest('[data-order-chip]')",script)
        self.assertIn('textSequence: [...localTextSequence]',script)
        self.assertIn('textSequenceFromDOM',script)
        self.assertIn('chip.dataset.assignedBoxId',script)
        self.assertNotIn("send('suspicious'",script)
        self.assertNotIn('suspiciousTokenIds',script)
        self.assertIn('suspiciouses:',script)
        self.assertIn('animateChipReflow',script)
        self.assertIn('captureChipRects',script)
        self.assertIn('getBoundingClientRect()',script)
        self.assertIn('translate3d(${dx}px, ${dy}px, 0)',script)
        self.assertRegex(script,r'duration:\s*180')
        self.assertRegex(script,r"easing:\s*'cubic-bezier\(.22, 1, .36, 1\)'")
        self.assertIn('chipReflowAnimations.get(chip)?.cancel()',script)
        self.assertIn("White: '#f4f4f5', Cyan: '#22d3ee', Amber: '#f59e0b'",script)
        self.assertIn("#bbox-color-palette input, #bbox-color-palette select",script)
        self.assertIn("root.addEventListener('bbox-color-change'",script)
        self.assertIn("new CustomEvent('bbox-color-change'",app_source)
        self.assertIn('detail: color, bubbles: true',app_source)
        self.assertIn('applyAnnotationColor',script)
        self.assertIn('updateExcludedChips',script)
        self.assertIn('Excluded from annotation data',script)
        self.assertNotIn('excluded-divider',script)
        self.assertIn("event.target.closest('#bbox-x1 input, #bbox-y1 input, #bbox-x2 input, #bbox-y2 input')",script)
        self.assertIn('localBoxes[activeBoxId].bbox = [x1, y1, x2, y2]',script)
        self.assertIn('syncingCoordinateControls = true',script)
        self.assertIn('if (syncingCoordinateControls || props.value.step !== 3',script)
        self.assertNotIn("send('add',",script)
        self.assertIn('syncExternalControls();',script)
        self.assertNotIn("addEventListener('dragover'",script)
        editor_css=(Path(__file__).resolve().parents[1]/'ui/assets/editor.css').read_text()
        self.assertIn('.order-chips {',editor_css)
        self.assertIn('flex-wrap: wrap',editor_css)
        self.assertNotIn('.order-chip-ghost',editor_css)
        self.assertIn('.order-chip.excluded {',editor_css)
        self.assertIn("content: '× Excluded'",editor_css)
        self.assertNotIn('.excluded-divider',editor_css)
        self.assertIn('overflow-x: auto',editor_css)
        self.assertIn('overflow-y: auto',editor_css)
        self.assertIn("Math.max(25, Math.min(500,",script)
        self.assertIn("group.dataset.status = status",script)
        workbench_css=(Path(__file__).resolve().parents[1]/'ui/assets/workbench.css').read_text()
        self.assertIn('#main-workspace { display: flex !important;',workbench_css)
        self.assertIn('color-scheme: dark',workbench_css)
        self.assertIn('button:not(:disabled):hover',workbench_css)
        self.assertIn('button.primary:not(:disabled):hover',workbench_css)
        self.assertNotIn('#bbox-color-palette label::after',workbench_css)
        self.assertNotIn('--box-swatch',workbench_css)
        editor_source=(Path(__file__).resolve().parents[1]/'ui/editor.py').read_text()
        self.assertNotIn('data-zoom="fit"',editor_source)
        self.assertNotIn('Fit image to view',editor_source)

    def test_character_chip_drag_has_pointer_scoped_central_cleanup(self):
        script=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertIn('const cleanupOrderDrag = (reason, commit = false) =>',script)
        self.assertIn('if (!state?.active || event.pointerId !== state.pointerId) return;',script)
        self.assertIn("document.addEventListener('pointerup', handleOrderPointerUp, true)",script)
        self.assertIn("document.addEventListener('pointercancel', handleOrderPointerCancel, true)",script)
        self.assertIn("removeEventListener('pointermove', handleOrderPointerMove, true)",script)
        self.assertIn("removeEventListener('pointerup', handleOrderPointerUp, true)",script)
        self.assertIn("removeEventListener('pointercancel', handleOrderPointerCancel, true)",script)
        self.assertIn("addEventListener('lostpointercapture', handleOrderLostPointerCapture)",script)
        self.assertNotIn('cloneNode(true)',script)
        self.assertNotIn('document.body.appendChild',script)
        self.assertNotIn('orderDragGhost',script)
        self.assertIn("if (orderDrag) cleanupOrderDrag('rerender', false)",script)
        self.assertIn('captureTarget: element',script)

    def test_repeated_alignment_characters_keep_distinct_chip_ids(self):
        state=new_state();state.update(image='12305.png',image_size=[100,100],
                                      image_url='image.jpg',current_step=4)
        set_verified_content(state,{},'永寺永')
        for x in (0,20,40):add_bbox(state,[x,0,x+10,10])
        refresh_bbox_validation(state);initialize_alignment(state)
        rendered=snapshot(state)
        markup=rendered['markup']
        self.assertEqual(markup.count('data-order-chip="1"'),3)
        self.assertIn('data-alignment-ready="false" aria-busy="true"',markup)
        self.assertEqual(rendered['alignmentTokenIds'],['1','2','3'])
        self.assertEqual(markup.count('data-token-id="1"'),1)
        self.assertEqual(markup.count('data-token-id="2"'),1)
        self.assertEqual(markup.count('data-token-id="3"'),1)
        self.assertEqual(markup.count('data-character="永"'),2)

    def test_status_order_uses_sidebar_source_and_dominant_image_grid(self):
        state=new_state();state.update(image='12305.png',image_size=[100,100],
                                      image_url='image.jpg',current_step=4)
        set_verified_content(state,{},'永寺樂')
        for x in (0,20,40):add_bbox(state,[x,0,x+10,10])
        refresh_bbox_validation(state);confirm_status(state);initialize_alignment(state)
        markup=snapshot(state)['markup']
        sidebar=source_text(state)
        self.assertIn('workbench-board status-order-board',markup)
        self.assertIn('class="order-editor"',markup)
        self.assertNotIn('class="source-preview"',markup)
        self.assertIn('class="sidebar-source-text"',sidebar)
        self.assertIn('永寺樂',sidebar)
        self.assertIn('viewBox="0 0 100 100"',markup)
        state['crop']=[10,15,80,90]
        state['current_step']=7
        cropped=snapshot(state)
        self.assertIn('viewBox="0 0 70.0 75.0"',cropped['markup'])
        self.assertIn('cropped review',cropped['markup'])
        self.assertEqual((cropped['width'],cropped['height']),(70,75))
        scaled=new_state();scaled.update(image='large.png',image_path='/missing/large.png',
            preview_dir='/private/tmp',image_size=[8000,6000],image_url='image.jpg',
            current_step=7,crop=[2000,1000,7000,5000],
            bounding_boxes={'1':{'bbox':[3000,2000,3200,2200],'status':'intact',
                'unknown':False,'unavailable_font':False,'expert_prediction':False,'suspicious':False}},
            annotations={'1':'永'},reading_order=[1])
        # Missing image paths keep this a pure overlay geometry check.
        scaled.pop('image_path');scaled.pop('preview_dir')
        scaled_render=snapshot(scaled)
        # The image is resized to fit a 4096 px crop. The box is scaled by
        # the same actual x/y resize factors before subtracting crop origin.
        self.assertIn('viewBox="0 0 4096.25 3276.666666666667"',scaled_render['markup'])
        self.assertIn('x="819.25" y="819.1666666666667"',scaled_render['markup'])
        css=(Path(__file__).resolve().parents[1]/'ui/assets/editor.css').read_text()
        self.assertIn('grid-template-columns: minmax(180px, 1fr) minmax(0, 2fr)',css)
        self.assertIn('.status-order-board .image-viewport',css)
        workbench_css=(Path(__file__).resolve().parents[1]/'ui/assets/workbench.css').read_text()
        self.assertIn('grid-template-columns: minmax(0, var(--sidebar-width)) minmax(0, 3fr)',workbench_css)
        self.assertIn('#control-panel { grid-column: 1; }',workbench_css)
        self.assertIn('#main-workspace { grid-column: 2; }',workbench_css)
        self.assertIn('@media (max-width: 767.98px)',workbench_css)

    def test_apply_and_next_snapshot_visible_text_cards(self):
        source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertIn("document.querySelector('#annotation-board')",source)
        self.assertIn("board?.querySelector('.order-chips')",source)
        self.assertIn("snapshot.textSequence = [...cards.querySelectorAll('[data-order-chip]')]",source)
        self.assertIn("snapshot.crop = bbox",source)
        self.assertIn("snapshot.boxes = boxes",source)
        self.assertIn("snapshot.statuses = statuses",source)
        self.assertIn('snapshot_board_state_js(4)',source)
        self.assertIn('snapshot_board_state_js(1)',source)
        self.assertIn("with gr.Accordion('text_annotations.json'",source)
        self.assertNotIn("with gr.Accordion('suspicious_details.json'",source)
        self.assertIn("with gr.Accordion('source_mismatches.json'",source)
        self.assertNotIn('load_suspicious_details(options.output_dir)',source)
        self.assertIn('collect_source_mismatches(',source)
        self.assertIn("value='White',show_label=False,interactive=True",source)
        self.assertNotIn("label='Outline color'",source)

    def test_compact_header_has_all_steps_and_no_draft_status(self):
        state=new_state();state.update(image='12305.jpg',current_step=2)
        markup=header(state)
        self.assertNotIn('Draft',markup)
        self.assertNotIn('save-indicator',markup)
        self.assertIn('<span class="brand-mark" aria-hidden="true">文</span>',markup)
        self.assertEqual(markup.count('class="stepper-item'),6)
        self.assertEqual(markup.count('class="stepper-label"'),6)
        self.assertIn('aria-current="step"',markup)
        for label in ('Image','Content','Bounding Boxes & Sort','Status & Order','Crop','Review'):
            self.assertIn(f'>{label}</span>',markup)

    def test_canvas_box_labels_are_order_driven(self):
        editor_source=(Path(__file__).resolve().parents[1]/'ui/editor.py').read_text()
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertIn('data-box-order-label="1"',editor_source)
        self.assertIn("g.querySelector(':scope > [data-box-order-label]')",editor_js)
        self.assertIn('text.textContent = labelText',editor_js)
        self.assertIn('assignedCharacter ? ` ${assignedCharacter}`',editor_js)
        self.assertIn('characterByBoxId.get(String(id))',editor_js)
        self.assertIn('alignmentTokenIds=list(map(str,s.get(\'text_token_ids\',[]))) if step==4 else []',editor_source)
        self.assertIn("if (localBoxes[id]) localBoxes[id].order = idx + 1",editor_js)

    def test_alignment_is_ready_before_chip_pointer_events(self):
        editor_source=(Path(__file__).resolve().parents[1]/'ui/editor.py').read_text()
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        editor_css=(Path(__file__).resolve().parents[1]/'ui/assets/editor.css').read_text()
        self.assertIn('data-alignment-ready="false" aria-busy="true"',editor_source)
        self.assertIn('alignmentStateReady = true',editor_js)
        self.assertIn('expectedIds.every(id => tokenIds.includes(id))',editor_js)
        self.assertIn('container?.dataset.alignmentReady !== \'true\'',editor_js)
        self.assertIn('new MutationObserver(() => {',editor_js)
        self.assertIn('.order-chips[data-alignment-ready="false"] { pointer-events: auto; }',editor_css)

    def test_box_count_changes_invalidate_mismatch_confirmation(self):
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertIn('mismatchConfirmationInvalidated = true',editor_js)
        self.assertIn('!mismatchConfirmationInvalidated',editor_js)
        self.assertIn('ctx,_,_=commit_frontend_boxes(ctx,selection)',app_source)
        self.assertIn('[session,mismatch_type,mismatch_note,selection_bridge]',app_source)
        self.assertIn('snapshot_board_state_js(3,deselect=True)',app_source)
        self.assertIn('board.dataset.localBoxesSnapshot = serializedSnapshot',editor_js)
        self.assertIn('snapshot = JSON.parse(board.dataset.localBoxesSnapshot)',app_source)
        self.assertIn('def clear_source_mismatch(ctx,selection):',app_source)
        self.assertIn('clear_source_mismatch,[session,selection_bridge]',app_source)

    def test_bounding_box_apply_button_is_removed(self):
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertNotIn("elem_id='apply-bbox-changes'",app_source)
        self.assertNotIn("#apply-bbox-changes",editor_js)
        self.assertNotIn('commitDraftState',editor_js)

    def test_matching_counts_allow_other_source_mismatch(self):
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertIn("mismatchGroup.style.display = 'block'",editor_js)
        self.assertNotIn("matched && !props.value.mismatchRecorded",editor_js)

    def test_clearing_mismatch_resets_frontend_confirmation(self):
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertIn('props.value.clearMismatchConfirmed',editor_js)
        self.assertIn('mismatchConfirmationInvalidated = true',editor_js)
        self.assertIn("result[8]['value']['clearMismatchConfirmed'] = True",app_source)

    def test_sort_selected_conflict_display_groups_consecutive_orders(self):
        editor_js=(Path(__file__).resolve().parents[1]/'ui/assets/editor.js').read_text()
        self.assertIn('const formatConflictOrderGroups = orders =>',editor_js)
        self.assertIn("groups.length > 4",editor_js)
        self.assertIn("groups.slice(0, 2), null, ...groups.slice(-2)",editor_js)
        self.assertIn('Conflicting orders: ${formatConflictOrderGroups(conflictOrders)}',editor_js)
        self.assertNotIn('Choose another starting number.',editor_js)

    def test_sidebar_has_bounded_scroll_and_aligned_action_controls(self):
        css=(Path(__file__).resolve().parents[1]/'ui/assets/workbench.css').read_text()
        self.assertIn('#header-stack {',css)
        self.assertIn('#topbar {',css)
        self.assertIn('#save-image { grid-column: 4; }',css)
        self.assertIn('#workflow-chrome {',css)
        self.assertIn('flex: 0 0 auto !important',css)
        self.assertIn('grid-template-rows: auto auto auto',css)
        self.assertIn('min-height: 100dvh',css)
        self.assertIn('overflow: visible',css)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr))',css)
        self.assertIn('#image-start {',css)
        self.assertIn('#control-panel .sidebar-action-stack {',css)
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertNotIn('Restore original content',app_source)
        self.assertNotIn('content-image-preview',app_source)
        self.assertIn('white-space: nowrap',css)
        self.assertIn('#confirm-source-mismatch',css)
        self.assertIn('justify-content: center',css)
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertIn('### Box-Content Validation',app_source)
        self.assertIn("('Missing Content','missing_text')",app_source)
        self.assertIn("('Extra Content','extra_text')",app_source)
        self.assertIn("elem_id='suspicious-toggle'",app_source)
        self.assertNotIn("Accordion('Source mismatch'",app_source)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr))',css)
        self.assertIn('.selection-guide {',css)
        self.assertIn('.selection-guide kbd {',css)
        self.assertIn('#selection-guide-host,',css)
        self.assertIn("elem_id='selection-guide-host'",app_source)
        self.assertIn('#validation-summary-host',css)
        self.assertIn("elem_id='validation-summary-host'",app_source)
        self.assertIn('#current-image-name code {',css)
        self.assertIn('-webkit-text-fill-color: var(--color-text) !important;',css)
        self.assertIn('.gradio-container textarea,',css)
        self.assertIn('.gradio-container input:-webkit-autofill,',css)
        self.assertIn('.gradio-container [role="listbox"],',css)
        self.assertIn('.gradio-container [role="option"][aria-selected="true"],',css)
        self.assertIn('.gradio-container select option,',css)
        self.assertIn('#final-preview .block-label,',css)
        self.assertIn('#final-preview [role="button"] {',css)
        self.assertIn('#final-preview .json-holder {',css)
        self.assertIn('#control-panel .sidebar-component {',css)
        self.assertIn('#control-panel .sidebar-component > .styler,',css)
        self.assertIn("'sidebar-component'",app_source)
        self.assertIn('#control-panel .sidebar-step-stack > * {',css)
        self.assertIn('.sidebar-step-stack > * > button { width: 100% !important;',css)
        self.assertIn('Selection Guide',app_source)
        self.assertIn('<kbd>Ctrl/Cmd + Click</kbd>',app_source)
        self.assertNotIn('### Box Actions',app_source)
        self.assertNotIn('.font-picker',css)
        self.assertNotIn("Accordion('Display font'",(Path(__file__).resolve().parents[1]/'app.py').read_text())

    def test_cli_paths_override_config_and_missing_paths_use_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            config=root/'config.json'
            config.write_text(json.dumps({
                'paths': {'output_json': 'source.json'},
                'gradio': {'image_dir': 'images', 'output_dir': 'annotations'},
                'records': {'content': {
                    'start_heading': 'Nguyên văn chữ Hán Nôm',
                    'section_headings': ['Nguyên văn chữ Hán Nôm'],
                }},
            }))
            override=root/'override-images'
            options=parser().parse_args([
                '--config',str(config),'--image-dir',str(override),
            ])
            resolve_app_paths(options)
            self.assertEqual(options.image_dir,str(override))
            self.assertEqual(options.source_json,str(root/'source.json'))
            self.assertEqual(options.output_dir,str(root/'annotations'))

            missing=root/'missing.json'
            explicit=parser().parse_args([
                '--config',str(missing),'--image-dir','images',
                '--source-json','source.json','--output-dir','annotations',
            ])
            with self.assertRaisesRegex(ValueError, 'Cannot read app config'):
                resolve_app_paths(explicit)

    def test_cli_lets_gradio_choose_an_available_port_by_default(self):
        self.assertIsNone(parser().parse_args([]).port)
        self.assertEqual(parser().parse_args(['--port','7861']).port,7861)

    def test_content_editor_only_selected_face_sections(self):
        titles=['Nguyên văn chữ Hán Nôm','Phiên âm Hán Việt','Dịch nghĩa','Toát yếu','Chú thích']
        app_source=(Path(__file__).resolve().parents[1]/'app.py').read_text()
        self.assertIn("field=gr.Dropdown(label='Section', filterable=False)",app_source)
        self.assertIn('outputs=[field_value,content_bridge,content_preview],queue=False,',app_source)
        self.assertIn('outputs=[content_bridge,content_preview], queue=False,',app_source)
        self.assertIn('parsed_path = json.loads(path) if isinstance(path, str) else path',app_source)
        self.assertIn('def commit_frontend_content(ctx,draft):',app_source)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            image=root/'12306.png';Image.new('RGB',(100,100),'white').save(image)
            source=root/'source.json'
            metadata_labels=['Tên bia','Địa điểm','Niên đại']
            record={'ten_bia':'Giữ tên bia','dia_diem':'Hà Nội','nien_dai':'Cảnh Hưng',
                    'extra':{'keep':42},'noi_dung':[
                {'ky_hieu':'12305','chuyen_muc':[{'tieu_de':titles[0],'van_ban':'文'}]},
                {'ky_hieu':'12306','chuyen_muc':[
                    {'tieu_de':title,'van_ban':'永寺樂' if title==titles[0] else title+' gốc','extra':'giữ nguyên'}
                    for title in reversed(titles)] + [{'tieu_de':'Mục khác','van_ban':'Giữ nguyên mục khác'}]}]}
            original=[record,{'noi_dung':[],'extra':'record khác'}]
            atomic_write(source,original)
            config=root/'config.json'
            atomic_write(config,{
                'records': {
                    'metadata': [
                        {'label':label,'field':field,'type':'string','required':True}
                        for label,field in zip(
                            metadata_labels,['ten_bia','dia_diem','nien_dai'])],
                    'content': {
                        'start_heading': titles[0], 'section_headings': titles}},
            })
            options=parser().parse_args(['--config',str(config),
                                        '--image-dir',str(root),'--source-json',str(source),
                                        '--output-dir',str(root/'out'),'--skip-detection'])
            app=create_app(options)
            functions=[f.fn for f in app.fns.values() if f.fn]
            open_image=next(f for f in functions if f.__name__=='open_image')
            advance_stream=next(f for f in functions if f.__name__=='next_with_progress')
            def save(ctx, draft):
                ctx=deepcopy(ctx)
                ctx['active']['current_step']=2
                return list(advance_stream(ctx,draft))[-1]
            result=open_image(dict(active=new_state(),drafts={}),str(image))
            ctx=result[0];choices=result[4]['choices']
            self.assertEqual(
                [label for label,_ in choices],
                metadata_labels+[SECTION_LABELS[t] for t in titles])
            self.assertEqual(result[5],'Giữ tên bia')
            preview=json.loads(result[6])
            self.assertEqual(
                [entry['tieu_de'] for entry in preview],metadata_labels+titles)
            self.assertNotIn('ten_bia',result[6])
            choice_by_label=dict(choices)
            metadata_path=choice_by_label['Tên bia']
            selected=choice_by_label['Dịch nghĩa']
            browser_draft=json.loads(result[44])
            browser_draft[metadata_path]['value']='Tên bia đã sửa'
            browser_draft[selected]['value']='Bản dịch\nđã sửa'
            draft_json=json.dumps(browser_draft,ensure_ascii=False)
            ctx=save(ctx,draft_json)[0]
            expected=deepcopy(original)
            expected[0]['ten_bia']='Tên bia đã sửa'
            expected[0]['noi_dung'][1]['chuyen_muc'][2]['van_ban']='Bản dịch\nđã sửa'
            self.assertEqual(json.loads(source.read_text()),original)
            self.assertEqual(ctx['active']['draft_content'],expected[0])
            self.assertEqual(ctx['active']['annotation_text'],'永寺樂')
            self.assertFalse((root/'out/.state/content.json').exists())
            # A submitted path cannot edit unconfigured metadata, headings,
            # other faces, or other sections.
            for path in (['extra','keep'],['noi_dung',0,'chuyen_muc',0,'van_ban'],
                         ['noi_dung',1,'chuyen_muc',0,'tieu_de'],
                         ['noi_dung',1,'chuyen_muc',5,'van_ban']):
                tampered=deepcopy(browser_draft)
                tampered[json.dumps(path,ensure_ascii=False)]={
                    'title':'Không hợp lệ','path':path,'value':'Không được ghi'}
                with self.assertWarnsRegex(UserWarning, 'incomplete or invalid'):
                    rejected=save(ctx,json.dumps(tampered,ensure_ascii=False))
                self.assertEqual(rejected[0]['active']['current_step'],2)
            self.assertEqual([field['title'] for field in content_fields(record,'12305')],[titles[0]])

    def test_next_verifies_current_editor_and_stays_on_verification_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            image=root/'12305.png';Image.new('RGB',(100,100),'white').save(image)
            source=root/'source.json'
            atomic_write(source,[{'noi_dung':[{'ky_hieu':'12305','chuyen_muc':[
                {'tieu_de':'Nguyên văn chữ Hán Nôm','van_ban':'永'}]}]}])
            options=parser().parse_args(['--image-dir',str(root),'--source-json',str(source),
                                        '--output-dir',str(root/'out'),'--skip-detection'])
            app=create_app(options)
            functions=[f.fn for f in app.fns.values() if f.fn]
            open_image=next(f for f in functions if f.__name__=='open_image')
            advance_stream=next(f for f in functions if f.__name__=='next_with_progress')
            def advance(*args):
                return list(advance_stream(*args))[-1]
            opened=open_image(dict(active=new_state(),drafts={}),str(image))
            ctx=opened[0];path=opened[4]['choices'][0][1]
            draft=json.loads(opened[44]);draft[path]['value']='永\n寺'
            draft=json.dumps(draft,ensure_ascii=False)
            with patch('annotation.workflow.set_verified_content',side_effect=OSError('verification failed')), self.assertLogs('app',level='ERROR'), self.assertWarnsRegex(UserWarning, 'verification failed'):
                failed=advance(ctx,draft)
            self.assertEqual(failed[0]['active']['current_step'],2)
            self.assertEqual(failed[4], gr.skip())
            self.assertEqual(failed[5], gr.skip())
            result=advance(failed[0],draft)
            self.assertEqual(result[0]['active']['current_step'],3)
            self.assertTrue(result[0]['active']['workflow']['content_verified'])
            self.assertEqual(json.loads(source.read_text())[0]['noi_dung'][0]['chuyen_muc'][0]['van_ban'],'永')
            self.assertFalse((root/'out/.state/content.json').exists())

    def current_app(self, root, text, skip_detection=True):
        image=root/'1.png';Image.new('RGB',(100,100),'white').save(image)
        source=root/'source.json'
        atomic_write(source,[{'noi_dung':[{'ky_hieu':'1','chuyen_muc':[
            {'tieu_de':'Nguyên văn chữ Hán Nôm','van_ban':text}]}]}])
        args=['--image-dir',str(root),'--source-json',str(source),'--output-dir',str(root/'out')]
        if skip_detection:args.append('--skip-detection')
        app=create_app(parser().parse_args(args))
        callbacks={f.fn.__name__:f.fn for f in app.fns.values() if f.fn}
        callbacks.update({f.fn.__defaults__[0]:f.fn for f in app.fns.values()
                          if f.fn and f.fn.__name__=='<lambda>' and f.fn.__defaults__
                          and f.fn.__defaults__[0] in ('save','back','undo')})
        opened=callbacks['open_image'](dict(active=new_state(),drafts={}),str(image))
        output_fn=next(f for f in app.fns.values() if f.fn and f.fn.__name__=='open_image')
        bridge=next(i for i,c in enumerate(output_fn.outputs) if c.elem_id=='content-draft-bridge')
        callbacks['draft_index']=bridge
        callbacks['advance']=lambda ctx,draft=None,**kw:list(callbacks['next_with_progress'](ctx,draft,**kw))[-1]
        def board(ctx,name,payload):
            payload=dict(payload,revision=ctx['active']['revision'],image=ctx['active']['image'])
            return callbacks['on_action'](ctx,gr.EventData(None,dict(action=name,payload=payload)))
        callbacks['board']=board
        return callbacks,opened,image

    def test_skip_detection_never_calls_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();cb,opened,_=self.current_app(root,'永')
            with patch('annotation.workflow.detect') as detector:
                ctx=cb['advance'](opened[0],opened[cb['draft_index']])[0]
                self.assertEqual(ctx['active']['current_step'],3)
                with self.assertWarnsRegex(UserWarning,'Detection is disabled'), self.assertLogs('app',level='ERROR'):
                    rejected=cb['board'](ctx,'detect',{})
                self.assertEqual(rejected[0]['active']['regions'],ctx['active']['regions'])
                self.assertFalse(rejected[0]['active']['detection_loaded'])
                boxes={'local_1':{'bbox':[0,0,10,10],'status':'intact','order':1}}
                ctx=cb['advance'](ctx,selection=json.dumps({'boxes':boxes}))[0]
                self.assertEqual(ctx['active']['current_step'],4)
                self.assertEqual(ctx['active']['annotations'],{'1':'永'})
                detector.assert_not_called()

    def test_ui_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();cb,opened,image=self.current_app(root,'永寺樂',skip_detection=False)
            with patch('annotation.workflow.detect',side_effect=RuntimeError('fixture model unavailable')), self.assertLogs('app',level='ERROR'), self.assertWarnsRegex(UserWarning,'fixture model unavailable'):
                stream=cb['next_with_progress'](opened[0],opened[cb['draft_index']])
                running=next(stream)
                self.assertEqual(running[2]['value'],'Running detection…')
                result=next(stream)
            ctx=result[0];self.assertEqual(ctx['active']['current_step'],3)
            boxes={f'local_{i}':{'bbox':[i*20,0,i*20+10,10],'status':'intact','order':i+1} for i in range(3)}
            # Complete frontend snapshots carry local additions, edits, deletions and sort.
            draft=json.dumps({'boxes':boxes,'selected':['local_0'],'active':'local_0'})
            result=cb['advance'](ctx,selection=draft);ctx=result[0]
            self.assertEqual(ctx['active']['current_step'],4)
            self.assertEqual(ctx['active']['annotations'],{'1':'永','2':'寺','3':'樂'})
            assignment={'textSequence':['永','樂','寺'],'tokenOrder':['1','3','2'],
                        'statuses':{'1':'intact','2':'damaged','3':'intact'},
                        'damage_levels':{'1':None,'2':2,'3':None},
                        'unknowns':{'1':False,'2':False,'3':False}}
            result=cb['apply_status_changes'](ctx,json.dumps(assignment),'intact');ctx=result[0]
            self.assertEqual(ctx['active']['annotations'],{'1':'永','2':'樂','3':'寺'})
            self.assertEqual(ctx['active']['bounding_boxes']['2']['damage_level'],2)
            ctx=cb['advance'](ctx)[0];self.assertEqual(ctx['active']['current_step'],6)
            result=cb['advance'](ctx,selection=json.dumps({'crop':[1,2,90,95]}));ctx=result[0]
            self.assertEqual(ctx['active']['current_step'],7)
            self.assertIn('cropped review',result[8]['value']['markup'])
            saved=result[20]['value']
            ctx=cb['save'](ctx)[0];self.assertEqual(ctx['active']['current_step'],1)
            payload=json.loads(cb['save_folder']())
            with zipfile.ZipFile(BytesIO(base64.b64decode(payload['content']))) as bundle:
                self.assertEqual(json.loads(bundle.read('text_annotations.json')),[saved])
                self.assertEqual(json.loads(bundle.read('inscription_content.json'))[0]['content']['Nguyên văn chữ Hán Nôm'],'永寺樂')
            reopened=cb['open_image'](ctx,str(image))
            ctx=cb['advance'](reopened[0],reopened[cb['draft_index']])[0]
            ctx=cb['advance'](ctx)[0]
            self.assertEqual(ctx['active']['current_step'],4)
            self.assertEqual(ctx['active']['annotations'],saved['annotations'])
            self.assertEqual(ctx['active']['bounding_boxes']['2']['damage_level'],2)
            self.assertEqual(ctx['active']['crop'],[1,2,90,95])

    def test_ui_source_mismatch_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();cb,opened,_=self.current_app(root,'永寺')
            ctx=cb['advance'](opened[0],opened[cb['draft_index']])[0]
            boxes={'local_1':{'bbox':[0,0,10,10],'status':'intact','order':1}}
            selection=json.dumps({'boxes':boxes,'active':'local_1','selected':['local_1']})
            with self.assertWarnsRegex(UserWarning,'require a note'), self.assertLogs('app',level='ERROR'):
                rejected=cb['confirm_source_mismatch'](ctx,'other','   ',selection)
            self.assertIsNone(rejected[0]['active']['source_mismatch'])
            result=cb['confirm_source_mismatch'](ctx,'other','source does not match the image',selection)
            ctx=result[0]
            self.assertTrue(ctx['active']['source_mismatch'])
            result=cb['advance'](ctx,selection=selection);ctx=result[0]
            self.assertEqual(ctx['active']['current_step'],7)
            self.assertEqual(ctx['active']['annotations'],{})
            ctx=cb['save'](ctx)[0]
            payload=json.loads(cb['save_folder']())
            with zipfile.ZipFile(BytesIO(base64.b64decode(payload['content']))) as bundle:
                self.assertEqual(json.loads(bundle.read('text_annotations.json')),[])
                mismatch=json.loads(bundle.read('source_mismatches.json'))[0]
            self.assertEqual(set(mismatch),{'image','inscription_code','issue_type','note','bounding_boxes'})
            self.assertEqual(mismatch['bounding_boxes'],{'1':{'bbox':[0,0,10,10]}})


if __name__=='__main__':unittest.main()
