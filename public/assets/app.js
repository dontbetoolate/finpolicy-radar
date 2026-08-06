const searchInput=document.getElementById('searchInput');
const categoryFilter=document.getElementById('categoryFilter');
const sourceFilter=document.getElementById('sourceFilter');
const importanceFilter=document.getElementById('importanceFilter');
const cards=[...document.querySelectorAll('.policy-card')];
const count=document.getElementById('resultCount');
const empty=document.getElementById('emptyState');
function norm(v){return (v||'').toLocaleLowerCase('zh-CN').trim()}
function apply(){
  const q=norm(searchInput.value), category=categoryFilter.value, source=sourceFilter.value, min=Number(importanceFilter.value||0);
  let shown=0;
  for(const card of cards){
    const ok=(!q||norm(card.dataset.title).includes(q))&&(!category||card.dataset.category.split('|').includes(category))&&(!source||card.dataset.source===source)&&Number(card.dataset.importance)>=min;
    card.hidden=!ok;if(ok)shown++;
  }
  count.textContent=`显示 ${shown} 条`;
  empty.hidden=shown!==0;
}
[searchInput,categoryFilter,sourceFilter,importanceFilter].forEach(el=>el.addEventListener('input',apply));
