const searchInput=document.getElementById('searchInput');
const sourceFilter=document.getElementById('sourceFilter');
const entityFilter=document.getElementById('entityFilter');
const typeFilter=document.getElementById('typeFilter');
const attentionFilter=document.getElementById('attentionFilter');
const dateFromFilter=document.getElementById('dateFromFilter');
const dateToFilter=document.getElementById('dateToFilter');
const clearFilters=document.getElementById('clearFilters');
const cards=[...document.querySelectorAll('.policy-card')];
const count=document.getElementById('resultCount');
const empty=document.getElementById('emptyState');
function norm(v){return (v||'').toLocaleLowerCase('zh-CN').trim()}
function apply(){
  const q=norm(searchInput.value), source=sourceFilter.value, entity=entityFilter.value, type=typeFilter.value, attention=attentionFilter.value, dateFrom=dateFromFilter.value, dateTo=dateToFilter.value;
  let shown=0;
  for(const card of cards){
    const date=card.dataset.date||'';
    const ok=(!q||norm(card.dataset.title).includes(q))&&(!source||card.dataset.source===source)&&(!entity||card.dataset.entities.split('|').includes(entity))&&(!type||card.dataset.type===type)&&(!attention||card.dataset.attention===attention)&&(!dateFrom||(date&&date>=dateFrom))&&(!dateTo||(date&&date<=dateTo));
    card.hidden=!ok;if(ok)shown++;
  }
  count.textContent=`显示 ${shown} 条`;
  empty.hidden=shown!==0;
  clearFilters.disabled=![searchInput,sourceFilter,entityFilter,typeFilter,attentionFilter,dateFromFilter,dateToFilter].some(el=>el.value);
}
[searchInput,sourceFilter,entityFilter,typeFilter,attentionFilter,dateFromFilter,dateToFilter].forEach(el=>el.addEventListener('input',apply));
clearFilters.addEventListener('click',()=>{
  [searchInput,sourceFilter,entityFilter,typeFilter,attentionFilter,dateFromFilter,dateToFilter].forEach(el=>{el.value=''});
  apply();
  searchInput.focus();
});
apply();
