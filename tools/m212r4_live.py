"""R4 development live runner. Captures all runs, uses existing configured client.
Independent SQL references are authored from specs, never from Artifact values.
"""
import argparse
import json
from pathlib import Path
from tools import m212_independent_acceptance as capture


def corpus():
    tasks=[]
    def add(id,q,**spec):
        s={'year':2025,'role':'BATTER','metric':'walks',**spec}
        tasks.append(dict(id=id,query=q,spec=s,semantic_ledger=s.copy()))
    add('scalar','Count José Ramírez batter walks in the 2025 regular season.',player=608070)
    add('pitcher','Report Zack Wheeler pitching average release speed for the 2025 regular season.',role='PITCHER',metric='avg_release_speed',player=554430)
    add('archive','请统计打者 Joey Votto 在2018年常规赛的保送数。',year=2018,player=458015)
    add('rank_b','Rank top 3 batters by walks in the 2025 regular season.',rank=3)
    add('rank_p','Rank top 3 pitchers by strikeouts in the 2025 regular season.',role='PITCHER',metric='strikeouts',rank=3)
    add('qual','Show top 4 batters by walks in the 2025 regular season with at least 237 PA.',rank=4,minimum=237,basis='PA')
    add('compound','Count batter plate appearances in 2025 regular season against lefties with two strikes.',metric='pa',filters=[['p_throws','=','L'],['strikes','=',2]])
    add('cjk','2025年常规赛，打者第7局的打席数是多少？',metric='pa',filters=[['inning','=',7]])
    add('split','For Xander Bogaerts as a batter in the 2025 regular season, split plate appearances by pitcher handedness.',metric='pa',player=593428,group=['p_throws'])
    add('hand_population','Count 2025 regular-season pitches for R pitchers.',role='PITCHER',metric='pitches',filters=[['p_throws','=','R']])
    add('count_state','Report batter walks in the 2025 regular season when balls are 3.',filters=[['balls','=',3]])
    add('exact_dates','Count Framber Valdez pitching pitches in regular season from 2025-07-11 through 2025-07-21 inclusive.',role='PITCHER',metric='pitches',player=664285,start='2025-07-11',end='2025-07-21')
    add('recent_pg','Count Framber Valdez pitching pitches in regular season from 2026-08-11 through 2026-08-21 inclusive.',year=2026,role='PITCHER',metric='pitches',player=664285,start='2026-08-11',end='2026-08-21')
    add('rate','Report José Ramírez batter BB% in the 2025 regular season.',metric='bb_pct',player=608070)
    add('historical_en','Count Gerrit Cole pitching strikeouts in the 2019 regular season.',year=2019,role='PITCHER',metric='strikeouts',player=543037)
    add('mixed','Report打者 José Ramírez 在2025年regular season的保送数。',player=608070)
    add('compound_many','Report batter walks AND strikeouts AND plate appearances in 2025 regular season against right-handed pitchers with two strikes in the eighth inning.',metric=['walks','strikeouts','pa'],filters=[['p_throws','=','R'],['strikes','=',2],['inning','=',8]])
    add('sample_group','Rank top 3 pitchers by average release speed in the 2025 regular season, group by pitch_type, with at least 307 PITCHES.',role='PITCHER',metric='avg_release_speed',rank=3,minimum=307,basis='PITCHES',group=['pitch_type'])
    add('comparison','Compare José Ramírez batter walks in the 2023 and 2025 regular seasons; report 2025 minus 2023.',player=608070,years=[2023,2025],difference=True)
    add('ambiguous','Report Smith batter walks in the 2025 regular season without guessing the identity.',negative='ambiguous')
    add('unsupported','Report batter walks in the 2025 regular season under a closed roof.',negative='unsupported')
    add('unresolved','Report batter walks in 2025 regular season on unusual commemorative occasions.',negative='unresolved')
    # These IDs select the capture harness's predefined two independent attempts.
    for t in tasks:
        t['id']={'qual':'qualified','rank_p':'pitcher_population','compound':'nested','archive':'archive_b'}.get(t['id'],t['id'])
    return tasks

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--ids',nargs='*');a=p.parse_args()
    capture.OUT=Path(a.directory);capture.OUT.mkdir(parents=True,exist_ok=True)
    tasks=corpus()
    if a.ids:tasks=[t for t in tasks if t['id'] in a.ids]
    capture.save('corpus.json',tasks)
    capture.references()
    capture.live()
