#!/usr/bin/env python3
import argparse, socket, time, hashlib, datetime as dt, subprocess, os

def ts(): return dt.datetime.now().astimezone().isoformat(timespec='seconds')
def fetch_pw():
 host=os.getenv('PISTAR_HOST')
 if not host:
  raise RuntimeError('PISTAR_HOST is required for this legacy ASCII test helper')
 user=os.getenv('PISTAR_USER','pi-star')
 env=os.environ.copy(); env['SSHPASS']=os.getenv('PISTAR_LOGIN_PASSWORD', '')
 return subprocess.check_output(['sshpass','-e','ssh','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/tmp/hermes_pistar_known_hosts',f'{user}@{host}',"awk -F= '/^Password=/{gsub(/\"/,\"\",$2); print $2; exit}' /etc/mmdvmhost"],env=env,text=True).strip()
def cfg(num,args):
 return (b'RPTC'+args.callsign.ljust(8)[:8].encode()+num+
         f'{int(args.rx_freq):09d}{int(args.tx_freq):09d}{int(args.tx_power):02d}{int(args.colorcode):02d}'.encode()+
         args.latitude.ljust(8)[:8].encode()+args.longitude.ljust(9)[:9].encode()+f'{int(args.height):03d}'.encode()+
         args.location.ljust(20)[:20].encode()+args.description.ljust(20)[:20].encode()+args.url.ljust(124)[:124].encode()+
         args.software_id.ljust(40)[:40].encode()+args.package_id.ljust(40)[:40].encode())

def main():
 p=argparse.ArgumentParser(); p.add_argument('--radio-id',type=int,required=True); p.add_argument('--duration',type=float,default=60)
 p.add_argument('--master',default='2503.master.brandmeister.network'); p.add_argument('--port',type=int,default=62031)
 p.add_argument('--callsign',default='R1BIN'); p.add_argument('--rx-freq',default='438025000'); p.add_argument('--tx-freq',default='430425000'); p.add_argument('--tx-power',default='1'); p.add_argument('--colorcode',default='1'); p.add_argument('--latitude',default='47.5297'); p.add_argument('--longitude',default='019.0575'); p.add_argument('--height',default='0'); p.add_argument('--location',default='Budapest'); p.add_argument('--description',default='Hermes BM monitor'); p.add_argument('--url',default='https://brandmeister.network/'); p.add_argument('--software-id',default='HermesHBPMonitor'); p.add_argument('--package-id',default='Hermes')
 args=p.parse_args(); pw=fetch_pw(); num=f'{args.radio_id:08x}'.encode(); addr=socket.getaddrinfo(args.master,args.port,socket.AF_INET,socket.SOCK_DGRAM)[0][4]
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('0.0.0.0',0)); s.settimeout(3)
 print(f'[{ts()}] ascii test id={args.radio_id} hex={num!r} local={s.getsockname()} master={addr}',flush=True)
 s.sendto(b'RPTL'+num,addr); print('sent',b'RPTL'+num,flush=True)
 deadline=time.time()+args.duration; state='login'
 while time.time()<deadline:
  try: pkt,a=s.recvfrom(2048)
  except socket.timeout: print('timeout'); continue
  print(f'[{ts()}] recv len={len(pkt)} {pkt!r}',flush=True)
  if pkt.startswith(b'MSTACK') and len(pkt)==22:
   salt=pkt[14:22]; dig=hashlib.sha256(salt+pw.encode()).hexdigest().encode(); msg=b'RPTK'+num+dig; s.sendto(msg,addr); print('sent RPTK len',len(msg),flush=True)
  elif pkt.startswith(b'MSTACK') and len(pkt)==14 and state=='login':
   c=cfg(num,args); print('send cfg len',len(c),c[:80]); s.sendto(c,addr); state='cfg'
  elif pkt.startswith(b'DMRD'):
   print('DMRD!')
 s.close()
if __name__=='__main__': main()
