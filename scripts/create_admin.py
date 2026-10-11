"""Create the first private administrator. Never accepts a password in arguments."""
import argparse,getpass,json,secrets,sys,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import db

def create(username,name,password):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}',username) or len(password)<12:raise ValueError('Invalid login or password length')
    db.initialize()
    with db.connection() as conn:
        conn.execute("UPDATE meta SET value=value WHERE key='data_revision'")
        if conn.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise ValueError('Login already exists; use the existing owner or explicit operator recovery')
        identity='u_'+secrets.token_hex(10)
        conn.execute("INSERT INTO users(id,username,email,name,password,role,admin_access,auth_kind,created_at,password_change_required) VALUES(?,?,?,?,?,'admin','owner','username',?,1)",(identity,username,identity+'@accounts.invalid',name,db.password_hash(password),db.now()))
        db.audit(conn,None,'account_bootstrapped',identity,{'method':'operator_cli'})
    return identity

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--username',required=True);parser.add_argument('--name',default='Администратор');parser.add_argument('--password-file',type=Path)
    args=parser.parse_args()
    password=args.password_file.read_text(encoding='utf-8').strip() if args.password_file else getpass.getpass('Temporary password (at least 12 characters): ')
    identity=create(args.username.lower(),args.name,password)
    print(json.dumps({'created':identity,'password_change_required':True}))
