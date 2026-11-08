"""Recover unique word grouping from retained MFA intervals, without changing CORE.

Numeric affixes may be split into multiple MFA words. No timestamps are taken
from CTC, interpolated, or assigned to unknown/spn intervals.
"""
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import math
import unicodedata
from decimal import Decimal


def _token(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value) if c.isalnum())


def recover_word_alignment(alignment, receipt):
    result = deepcopy(alignment)
    if not isinstance(receipt,dict): return result
    words, phones = receipt.get('word_intervals'), receipt.get('phone_intervals')
    if not isinstance(words,list) or not isinstance(phones,list): return result
    if len(words)>2048 or len(phones)>8192: return result
    groups = defaultdict(list)
    for row in result['rows']: groups[row['word_index']].append(row)
    groups = sorted(groups.items())
    if [i for i,_ in groups] != list(range(len(groups))): return result
    if any(len({r['word'] for r in rows})!=1 for _,rows in groups): return result
    words = [w for w in words if isinstance(w.get('label'),str) and w['label'].strip()]
    expected = [_token(rows[0]['word']) for _,rows in groups]
    actual = [_token(w['label']) for w in words]
    if not all(expected) or not all(actual) or len(expected)>256: return result

    @lru_cache(None)
    def paths(i,j):
        if i==len(expected): return ((),) if j==len(actual) else ()
        found=[];joined=''
        for end in range(j,min(len(actual),j+4)):
            joined+=actual[end]
            if not expected[i].startswith(joined): break
            if joined==expected[i]:
                for tail in paths(i+1,end+1):
                    found.append(((j,end+1),)+tail)
                    if len(found)==2: return tuple(found)
        return tuple(found)

    matches=paths(0,0)
    if len(matches)!=1: return result
    for (_,rows),(first,last) in zip(groups,matches[0]):
        start,end=words[first]['start_s'],words[last-1]['end_s']
        if not (type(start) in (int,float,Decimal) and type(end) in (int,float,Decimal)
                and math.isfinite(start) and math.isfinite(end) and 0<=start<end<=120): continue
        selected=[p for p in phones if p.get('label') and p['start_s']>=start and p['end_s']<=end]
        if len(selected)!=len(rows) or any(p['label'] in {'spn','sil','sp','<unk>'} for p in selected): continue
        if any(not start<=p['start_s']<p['end_s']<=end for p in selected): continue
        if any(a['end_s']>b['start_s'] for a,b in zip(selected,selected[1:])): continue
        for row,phone in zip(rows,selected):
            # The old sidecar loses the vowel's original ownership confidence.
            # Recover consonants only; never manufacture a precise vowel span.
            if row['role'] not in {'onset','coda'} or row['localization_status']!='WORD_TEXT_UNRESOLVED': continue
            row.update(localization_status='EXACT_COUNT_WORD_INDEX',mfa_ipa=phone['label'],
                word_start_s=float(start),word_end_s=float(end),
                phone_start_s=float(phone['start_s']),phone_end_s=float(phone['end_s']))
    return result
