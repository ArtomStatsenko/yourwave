"""Monotonic lyric-to-ASR alignment; timings come from ASR, not acoustic forced alignment."""
import re
import unicodedata

def tokens(text):
    return [re.sub(r"[^\w]", "", w) for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)?", unicodedata.normalize("NFKC", text).casefold().replace("ё", "е"))]

def chorus(label):
    label = re.sub(r"[\d:()–—-]+", " ", label.casefold())
    return bool(re.fullmatch(r"\s*(?:final\s+)?(?:chorus|припев)(?:\s+final)?\s*", label))

def parse(text):
    lines=[];section=-1;label='unlabelled';offset=0
    for raw in text.splitlines():
        raw=raw.strip()
        if not raw:continue
        if raw.startswith('[') and raw.endswith(']'):
            section+=1;label=raw[1:-1].strip();continue
        words=tokens(raw)
        if words:
            lines.append({'section':section,'label':label,'start_token':offset,'words':words})
            offset+=len(words)
    return lines


def align(reference,heard):
    """Needleman–Wunsch alignment over the whole token sequence, preserving order."""
    n,m=len(reference),len(heard)
    if n>5000 or m>5000:raise ValueError('Trial limited to 5000 tokens per recording')
    parents=[bytearray(m+1) for _ in range(n+1)]
    previous=[-2*j for j in range(m+1)]
    for j in range(1,m+1):parents[0][j]=3
    for i in range(1,n+1):
        current=[-2*i]+[0]*m;parents[i][0]=2
        for j in range(1,m+1):
            diagonal=previous[j-1]+(3 if reference[i-1]==heard[j-1] else -2)
            delete=previous[j]-2;insert=current[j-1]-2
            if diagonal>=delete and diagonal>=insert:current[j]=diagonal;parents[i][j]=1
            elif delete>=insert:current[j]=delete;parents[i][j]=2
            else:current[j]=insert;parents[i][j]=3
        previous=current
    matched={};i,j=n,m
    while i or j:
        direction=parents[i][j]
        if direction==1:
            if reference[i-1]==heard[j-1]:matched[i-1]=j-1
            i-=1;j-=1
        elif direction==2:i-=1
        elif direction==3:j-=1
        else:raise ValueError('Invalid alignment traceback')
    return matched


def map_sections(lines,asr_words,duration):
    heard=[]
    for word in asr_words:
        if not 0<=word['start']<=word['end']<=duration+.5:raise ValueError('ASR word outside recording')
        for token in tokens(word['word']):heard.append({**word,'token':token})
    if any(a['start']>b['start'] for a,b in zip(heard,heard[1:])):raise ValueError('Non-monotonic ASR timestamps')
    reference=[word for line in lines for word in line['words']]
    mapping=align(reference,[word['token'] for word in heard])
    overall=len(mapping)/max(1,len(reference));groups={}
    for line in lines:groups.setdefault(line['section'],[]).append(line)
    sections=[]
    for section,group in groups.items():
        if not chorus(group[0]['label']):continue
        candidates=[]
        for count in (2,3,4):
            if len(group)<count:continue
            first=group[0]['start_token'];last=group[count-1]['start_token']+len(group[count-1]['words'])-1
            coverage=sum(i in mapping for i in range(first,last+1))/(last-first+1)
            if overall<.55 or coverage<.8 or first not in mapping or last not in mapping:continue
            start=max(0,heard[mapping[first]]['start']-.08);end=min(duration,heard[mapping[last]]['end']+.15)
            if 5<=end-start<=12:
                candidates.append({'start':round(start,3),'end':round(end,3),'text_coverage':round(coverage,3),'line_count':count})
        indices=[i for line in group for i in range(line['start_token'],line['start_token']+len(line['words']))]
        mapped=[mapping[i] for i in indices if i in mapping]
        record={'section_index':section,'label':group[0]['label'],'section_text_coverage':round(len(mapped)/max(1,len(indices)),3),'status':'no_candidate'}
        if mapped:record.update(section_start=round(heard[min(mapped)]['start'],3),section_end=round(heard[max(mapped)]['end'],3))
        if candidates:record.update(min(candidates,key=lambda c:(abs(c['end']-c['start']-8),-c['text_coverage'])),status='candidate')
        sections.append(record)
    return {'overall_text_coverage':round(overall,3),'reference_tokens':len(reference),'recognized_tokens':len(heard),'matched_tokens':len(mapping),'sections':sections}
