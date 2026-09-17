import json,psycopg2,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from app.config import settings as s
c=psycopg2.connect(host=s.postgres_host,port=s.postgres_port,dbname=s.postgres_db,user=s.postgres_user,password=s.postgres_password,connect_timeout=5)
cur=c.cursor();cur.execute("SELECT current_user,current_setting('transaction_read_only')");print(json.dumps({'runtime':cur.fetchall()}),flush=True)
for name,sql in [('CREATE','CREATE TABLE public.semantic_review_probe(x int)'),('INSERT','INSERT INTO statcast_pitches(game_pk) SELECT -1 WHERE false'),('UPDATE','UPDATE statcast_pitches SET game_pk=game_pk WHERE false'),('DELETE','DELETE FROM statcast_pitches WHERE false'),('DROP','DROP TABLE IF EXISTS public.semantic_review_probe')]:
 try:cur.execute(sql);print(json.dumps({'probe':name,'result':'UNEXPECTED_SUCCESS'}),flush=True)
 except Exception as e:print(json.dumps({'probe':name,'result':type(e).__name__,'sqlstate':e.pgcode}),flush=True)
 finally:c.rollback()
c.close()
