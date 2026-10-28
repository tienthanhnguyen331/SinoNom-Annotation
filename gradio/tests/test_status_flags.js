// Execute the production preview and radio handlers against a small SVG DOM fixture.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = fs.readFileSync(require('node:path').join(__dirname, '../ui/assets/editor.js'), 'utf8');
const extract = (start, end) => script.slice(script.indexOf(start), script.indexOf(end, script.indexOf(start)));
class Node {
  constructor() { this.attrs = {}; this.dataset = {}; this.style = {}; this.children = []; this.classes=new Set(); this.classList = {contains:key=>this.classes.has(key),toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key),remove:key=>this.classes.delete(key)}; }
  setAttribute(k,v) {this.attrs[k]=String(v);}
  getAttribute(k) {return this.attrs[k];}
  removeAttribute(k) {delete this.attrs[k];}
  appendChild(n) {n.parent=this;this.children.push(n);}
  remove() {this.parent.children=this.parent.children.filter(n=>n!==this);}
  querySelector(selector) {
    if (selector.startsWith('rect')) return this.rect;
    if (selector === ':scope > [data-box-order-label]' || selector === '[data-box-order-label]') return this.label;
    if (selector === '[data-unknown-mark]') return this.children.find(n=>n.dataset.unknownMark);
    return null;
  }
}
const group = new Node();group.rect=new Node();group.label=new Node();group.dataset.boxId='1';
Object.entries({x:10,y:20,width:30,height:40}).forEach(([k,v])=>group.rect.setAttribute(k,v));
const chip=new Node();chip.dataset={assignedBoxId:'1',character:'永'};const box={bbox:[10,20,40,60],status:'intact',unknown:false,unavailable_font:false,expert_prediction:false,suspicious:false};
const context = {localBoxes:{'1':box},groupFor:()=>group,props:{value:{step:4}},
  selectedIds:new Set(['1']), annotationColor:'#22d3ee', applyAnnotationColor:()=>{}, element:{querySelector:selector=>selector==='.status-legend'?null:chip,querySelectorAll:selector=>selector.startsWith('.annotation-canvas')?[group]:selector==='[data-order-chip]'?[]:[chip]}, document:{createElementNS:()=>new Node()},
  activeBoxId:'1',syncExternalControls:()=>{},assert};
vm.createContext(context);
vm.runInContext(extract('const statusColor =', 'const applyAnnotationColor =') +
  extract('const renderLocalStatus =', 'const updateCanvasLabels ='), context);
const run = code => vm.runInContext(code,context);
const radioHandlers=script.slice(script.indexOf("  const suspicious = event.target.closest('#suspicious-toggle input');"),
  script.indexOf("\n});",script.indexOf("  const suspicious = event.target.closest('#suspicious-toggle input');")));
vm.runInContext(extract('function renderSuspiciousPreview()', 'const orderRows ='),context);
run(`function radio(selector,value) { const event={target:{closest:s=>s===selector?{value,checked:value===true}:null}}; ${radioHandlers} }`);
run("renderLocalStatus('1','intact',false)");
assert.equal(group.rect.attrs.stroke,'#22c55e');assert.equal(group.label.attrs.fill,'#ffffff');
run("radio('#unavailable-font-radio input','True')");
assert.equal(group.label.attrs.fill,'#ec4899');assert.equal(group.rect.attrs['fill-opacity'],'.20');
assert.equal(box.status,'intact');
run("radio('#expert-prediction-radio input','True')");
assert.equal(box.status,'damaged');assert.equal(group.rect.attrs.stroke,'#facc15');assert.equal(group.label.attrs.fill,'#ec4899');
run("radio('#status-radio input','intact')");
assert.equal(box.status,'intact');assert.equal(group.rect.attrs.stroke,'#facc15');
run("radio('#unavailable-font-radio input','False')");
assert.equal(group.label.attrs.fill,'#facc15');assert.equal(chip.style.color,'#facc15');
run("radio('#status-radio input','damaged'); radio('#unknown-radio input','True')");
assert.equal(box.expert_prediction,false);assert.equal(box.unavailable_font,false);
assert.equal(group.rect.attrs.stroke,'#ef4444');assert.equal(group.label.attrs.fill,'#ffffff');
assert.equal(group.querySelector('[data-unknown-mark]').textContent,'?');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-size'],'24');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-weight'],'700');
// Review coordinates differ from original bbox after crop/resize.
Object.entries({x:2,y:3,width:15,height:20}).forEach(([k,v])=>group.rect.setAttribute(k,v));
run("renderLocalStatus('1','damaged',true)");
assert.equal(group.querySelector('[data-unknown-mark]').attrs.x,'9.5');
assert.equal(group.querySelector('[data-unknown-mark]').attrs.y,'13');
assert.equal(group.querySelector('[data-unknown-mark]').attrs['font-size'],'12');
run("radio('#unavailable-font-radio input','True')");
assert.equal(box.unknown,false);assert.equal(group.querySelector('[data-unknown-mark]'),undefined);
run("radio('#expert-prediction-radio input','True'); radio('#expert-prediction-radio input','False')");
assert.equal(box.status,'damaged');assert.equal(box.unavailable_font,true);
group.rect.dataset.missing='1';
run("radio('#expert-prediction-radio input','True'); renderLocalStatus('1','damaged',true)");
assert.equal(box.expert_prediction,false);assert.equal(box.unavailable_font,false);assert.equal(box.unknown,false);
// Regression: hydration/selection must preserve 20% pink in both screens.
delete group.rect.dataset.missing;
for (const step of [4,7]) {
  context.props.value.step=step;
  for (const [font,expert,unknown,suspicious,missing,status,stroke,label,fill,opacity] of [
    [false,false,false,false,false,'intact','#22c55e','#ffffff','#22c55e','.04'],
    [false,false,false,false,false,'damaged','#ef4444','#ffffff','#ef4444','.04'],
    [true,false,false,false,false,'intact','#22c55e','#ec4899','#ec4899','.20'],
    [true,false,false,false,false,'damaged','#ef4444','#ec4899','#ec4899','.20'],
    [false,true,false,false,false,'intact','#facc15','#facc15','#facc15','.04'],
    [false,true,false,false,false,'damaged','#facc15','#facc15','#facc15','.04'],
    [true,true,false,false,false,'intact','#facc15','#ec4899','#ec4899','.20'],
    [false,false,true,false,false,'damaged','#ef4444','#ffffff','#ef4444','.04'],
    [true,true,false,true,false,'damaged','#facc15','#ec4899','#ec4899','.20'],
    [false,false,false,true,false,'intact','#22c55e','#ffffff','#facc15','.20'],
    [false,false,false,false,true,'intact','#22c55e','#ffffff','#e5e7eb','.30'],
  ]) {
    Object.assign(box,{status,unavailable_font:font,expert_prediction:expert,unknown,suspicious});
    group.classList.toggle('suspicious-region',suspicious);
    if(missing) group.rect.dataset.missing='1'; else delete group.rect.dataset.missing;
    run("renderLocalStatus('1',localBoxes['1'].status,localBoxes['1'].unknown); renderSelection(false)");
    assert.equal(group.rect.attrs.fill,fill);
    assert.equal(group.rect.attrs['fill-opacity'],opacity);
    assert.equal(group.rect.attrs.stroke,stroke);
    assert.equal(group.label.attrs.fill,label);
    assert.equal(Boolean(group.querySelector('[data-unknown-mark]')),unknown);
  }
}
delete group.rect.dataset.missing;
group.classList.toggle('suspicious-region',false);
// Reordering/label refresh must preserve special label color and text size.
context.props.value.width=100;context.props.value.height=100;
box.order=1;
vm.runInContext(extract('const updateCanvasLabels =','const hydrateLocalState ='),context);
for (const [font,expert,color] of [[true,true,'#ec4899'],[false,true,'#facc15'],[false,false,'#ffffff']]) {
  context.props.value.step=4;box.unavailable_font=font;box.expert_prediction=expert;
  run('updateCanvasLabels(); renderSelection(false)');
  assert.equal(group.label.attrs.fill,color);
  assert.equal(group.label.attrs['font-size'],'9');
}
// True/False radio acts on the box and excludes Unknown in both directions.
box.status='damaged';box.unknown=true;box.suspicious=false;
run("radio('#suspicious-toggle input','True')");
assert.equal(box.suspicious,true);assert.equal(box.unknown,false);
assert.equal(group.rect.attrs['fill-opacity'],'.20');
assert.equal(chip.classList.contains('suspicious'),true);
run("radio('#unknown-radio input','True')");
assert.equal(box.suspicious,false);assert.equal(chip.classList.contains('suspicious'),false);
run("radio('#suspicious-toggle input','True'); radio('#status-radio input','intact')");
assert.equal(box.suspicious,true);
chip.classList.toggle('excluded',true);
run("radio('#suspicious-toggle input','False')");
assert.equal(box.suspicious,true);
chip.classList.toggle('excluded',false);
// Physical palette changes must be scoped to the geometry editor (step 3).
assert.match(extract('const applyAnnotationColor =','const readAnnotationColor ='), /props.value.step !== 3/);
console.log('PASS: radio handlers, all status visuals after selection in steps 4/7, label refresh and cropped/scaled question mark');

chip.dataset.character='□';
Object.assign(box,{status:'intact',unknown:false,suspicious:true,unavailable_font:true});
run("renderLocalStatus('1','intact',false)");
assert.equal(box.status,'damaged');assert.equal(box.unknown,true);
assert.equal(box.suspicious,false);assert.equal(box.unavailable_font,false);
chip.dataset.character='@';
run("renderLocalStatus('1','intact',true)");
assert.equal(box.unknown,false);assert.equal(box.unavailable_font,true);
assert.equal(chip.style.color,'#ec4899');

run("radio('#status-radio input','damaged'); radio('#damage-level-radio input','2')");
assert.equal(box.damage_level,2);
run("radio('#status-radio input','intact')");
assert.equal(box.damage_level,null);
run("radio('#status-radio input','damaged')");
assert.equal(box.damage_level,null);
run("radio('#damage-level-radio input','3'); radio('#unknown-radio input','True')");
assert.equal(box.damage_level,3);
console.log('PASS: damage level draft changes, intact reset, and independent unknown flag');

// Mandatory placeholder flags must also be locked in the actual sidebar controls.
const policyRadios = Object.fromEntries([
  ['#status-radio', ['intact', 'damaged']],
  ['#unknown-radio', ['False', 'True']],
  ['#unavailable-font-radio', ['False', 'True']],
  ['#expert-prediction-radio', ['False', 'True']],
  ['#suspicious-toggle', ['False', 'True']],
  ['#damage-level-radio', ['Unselected', '1', '2', '3']],
].map(([selector, values]) => {
  const radios = values.map(value => ({value, checked: false, disabled: false,
    label: new Node(), closest() { return this.label; },
    click() { if (!this.disabled) { radios.forEach(radio => { radio.checked = radio === this; }); } },
  }));
  return [selector, radios];
}));
const policyBox = {bbox: [0, 0, 10, 10], status: 'damaged', damage_level: 2,
  unknown: true, unavailable_font: false, expert_prediction: false, suspicious: false};
const policyChip = new Node(); policyChip.dataset = {character: '□', tokenId: '1'};
const unknownControl = new Node();
const policyRoot = {
  querySelectorAll(selector) {
    return selector.split(',').flatMap(part => policyRadios[part.trim().replace(/ input$/, '')] || []);
  },
  querySelector(selector) {
    if (selector === '#unknown-radio') return unknownControl;
    const match = selector.match(/^(#[\w-]+) input\[value="([^"]+)"\]$/);
    return match ? policyRadios[match[1]]?.find(input => input.value === match[2]) : null;
  },
};
const policyContext = {
  props: {value: {step: 4}}, localBoxes: {'1': policyBox}, activeBoxId: '1', activeTokenId: null,
  selectedIds: new Set(['1']), localTextSequence: ['□'], root: policyRoot,
  syncingStatusControl: false, setInputValue: () => {}, updateValidationSummary: () => {},
  scheduleDraftCheckpoint: () => {},
  serializeLocalBoxes: () => ({'1': policyBox}), renderLocalStatus: () => {},
  element: {querySelector: selector => selector.startsWith('[data-order-chip]') ? policyChip : null,
    querySelectorAll: () => []},
};
vm.createContext(policyContext);
vm.runInContext(extract('const syncExternalControls =', 'const renderLocalStatus ='), policyContext);
const syncPolicy = () => vm.runInContext('syncExternalControls()', policyContext);
const isLocked = selector => policyRadios[selector].every(input => input.disabled);
const checkedValue = selector => policyRadios[selector].find(input => input.checked)?.value;
syncPolicy();
for (const selector of ['#status-radio', '#unknown-radio', '#unavailable-font-radio', '#expert-prediction-radio', '#suspicious-toggle']) {
  assert.equal(isLocked(selector), true, `□ must lock ${selector}`);
}
assert.equal(checkedValue('#unknown-radio'), 'True');
assert.equal(checkedValue('#status-radio'), 'damaged');
assert.equal(isLocked('#damage-level-radio'), false);
policyChip.dataset.character = '@';
Object.assign(policyBox, {status: 'intact', unknown: false, unavailable_font: true});
syncPolicy();
assert.equal(isLocked('#unavailable-font-radio'), true);
assert.equal(checkedValue('#unavailable-font-radio'), 'True');
assert.equal(isLocked('#unknown-radio'), true);
assert.equal(checkedValue('#unknown-radio'), 'False');
assert.equal(isLocked('#status-radio'), false);
assert.equal(isLocked('#expert-prediction-radio'), false);
policyChip.dataset.character = '永';
Object.assign(policyBox, {status: 'damaged', unknown: false, unavailable_font: false});
syncPolicy();
for (const selector of ['#status-radio', '#unknown-radio', '#unavailable-font-radio', '#expert-prediction-radio', '#suspicious-toggle']) {
  assert.equal(isLocked(selector), false, `Normal character must unlock ${selector}`);
}
policyContext.activeBoxId = null;
syncPolicy();
assert.equal(Object.keys(policyRadios).every(isLocked), true);
console.log('PASS: □/@ mandatory sidebar flags stay locked; normal characters unlock; no-selection is safe');
