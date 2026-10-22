"""Small, literal source fact extraction; no price direction or output rewriting."""
import re
import unicodedata


def normalized_party(value: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKD',value)
                   if not unicodedata.combining(char)).casefold()


def _text(item: dict) -> str:
    return '\n'.join(dict.fromkeys(str(item.get(key) or '').strip()
                                   for key in ('title','summary','body','fact_excerpt','facts')
                                   if item.get(key)))


def award_roles(item: dict) -> list[dict]:
    """Only explicit entrusts/awarded-by relations; ambiguous parties stay open."""
    text=_text(item);pairs=[]
    entrust=re.compile(r'(?m)^(?P<grantor>[^:\n。;]{2,100}?)\s+(?:entrusts|(?:has\s+)?entrusted)\s+(?P<recipient>[^,\n。;]{2,100}?)\s+with\b[^\n。]{0,100}',re.I)
    for match in entrust.finditer(text):
        pairs.append((match['grantor'].strip(),match['recipient'].strip(),match.group().strip()))
    recipient=re.compile(r'(?:^|[.\n])\s*(?P<party>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-öø-ÿ0-9&’\'() -]{1,80}?)\s+(?:has|have)\s+(?:secured|won|received)\b[^.\n]{0,150}',re.M)
    grantor=re.compile(r'\bawarded\s+by\s+(?P<party>[A-ZÀ-ÖØ-Þ][^,.;\n。]{1,90})',re.I)
    recipients={normalized_party(m['party'].strip()):m for m in recipient.finditer(text)}
    grantors={normalized_party(m['party'].strip()):m for m in grantor.finditer(text)}
    # Do not join the parties of multiple independent contracts in an article.
    if len(recipients)==len(grantors)==1:
        recv=next(iter(recipients.values()));give=next(iter(grantors.values()))
        pairs.append((give['party'].strip(),recv['party'].strip(),give.group().strip()))
    result=[];seen=set()
    for give,recv,quote in pairs:
        key=(normalized_party(give),normalized_party(recv))
        if key in seen or key[0]==key[1]:continue
        seen.add(key)
        result.append({'grantor':give,'recipient':recv,'source_quote':quote,
                       'rendering_zh':f'原报道授予/委托方为{give}，获授/承接方为{recv}'})
    return result


def reported_source_facts(item: dict) -> dict:
    """Copy the original tense, not a claim that a future action remains undone."""
    result={};roles=award_roles(item)
    if roles:result['contract_party_roles']=roles
    text=_text(item)
    action=re.search(r'\bwill(?:\s+now)?\s+be\s+(?:permanently\s+)?plugged\s+and\s+abandoned\b',text,re.I)
    completion=re.search(r'\b(?:has|have)\s+(?:now\s+|already\s+)?been\s+(?:permanently\s+)?plugged\s+and\s+abandoned\b|\bwas\s+(?:permanently\s+)?plugged\s+and\s+abandoned\b',text,re.I)
    if action and not completion:
        result['well_action']={'action':'permanent_plugging_and_abandonment','tense':'future_in_report',
                               'source_quote':action.group(),'published_at':item.get('published_at',item.get('time')),
                               'rendering_zh':'原报道说该井将永久封堵并弃井；该条来源未给完成更新，不能仅从报道日期已过推断已完成'}
    return result
