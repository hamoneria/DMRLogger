#!/usr/bin/env python3
from __future__ import annotations
import argparse, socket, time, datetime as dt

DMRD=b'DMRD'
DMRC=b'DMRC'
DMRP=b'DMRP'
DMRB=b'DMRB'

def b4(n:int)->bytes: return int(n).to_bytes(4,'big')
def int3(b:bytes)->int: return int.from_bytes(b,'big')
def int4(b:bytes)->int: return int.from_bytes(b,'big')
def ts(): return dt.datetime.now().astimezone().isoformat(timespec='seconds')
def fixed(s,n): return str(s).ljust(n)[:n].encode()

def dmrc(args):
    # MMDVMHost Direct DMR Network config packet, 119 bytes.
    slots = args.slots
    return b''.join([
        DMRC, b4(args.radio_id), fixed(args.callsign,8),
        f'{int(args.rx_freq):09d}'.encode(), f'{int(args.tx_freq):09d}'.encode(),
        f'{min(int(args.tx_power),99):02d}'.encode(), f'{int(args.colorcode):02d}'.encode(),
        slots.encode()[:1], fixed(args.version,40), fixed(args.software,40)
    ])

def parse_dmrd(pkt:bytes):
    bits=pkt[15]
    if bits & 0x40: ctype='unit'
    elif (bits & 0x23)==0x23: ctype='vcsbk'
    else: ctype='group'
    return dict(seq=pkt[4],src=int3(pkt[5:8]),dst=int3(pkt[8:11]),peer=int4(pkt[11:15]),slot=2 if bits&0x80 else 1,call_type=ctype,frame_type=(bits&0x30)>>4,dtype=bits&0x0f,stream=int4(pkt[16:20]))

def main():
    p=argparse.ArgumentParser(description='Minimal MMDVM Direct protocol listener (DMRC/DMRP/DMRD)')
    p.add_argument('--master',default='2503.master.brandmeister.network')
    p.add_argument('--port',type=int,default=62031)
    p.add_argument('--radio-id',type=int,required=True)
    p.add_argument('--tg',type=int,default=2501)
    p.add_argument('--duration',type=float,default=120)
    p.add_argument('--callsign',default='R1BIN')
    p.add_argument('--rx-freq',default='438025000')
    p.add_argument('--tx-freq',default='430425000')
    p.add_argument('--tx-power',default='01')
    p.add_argument('--colorcode',default='1')
    p.add_argument('--slots',default='3', help='MMDVMHost duplex slots: 3=TS1+TS2, 1=TS1, 2=TS2, 4=DMO')
    p.add_argument('--version',default='MMDVMHost-20260220')
    p.add_argument('--software',default='MMDVM_MMDVM_HS')
    args=p.parse_args()
    addr=socket.getaddrinfo(args.master,args.port,socket.AF_INET,socket.SOCK_DGRAM)[0][4]
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); sock.bind(('0.0.0.0',0)); sock.settimeout(1)
    print(f'[{ts()}] MMDVM Direct listen id={args.radio_id} local={sock.getsockname()} master={addr} tg={args.tg}', flush=True)
    cfg=dmrc(args)
    print(f'[{ts()}] sending DMRC len={len(cfg)}', flush=True)
    sock.sendto(cfg,addr)
    deadline=time.time()+args.duration; next_cfg=time.time()+10; total=sel=0
    streams={}
    try:
      while time.time()<deadline:
        if time.time()>=next_cfg:
            sock.sendto(cfg,addr); next_cfg=time.time()+10; print(f'[{ts()}] sent DMRC keepalive', flush=True)
        try: pkt,a=sock.recvfrom(2048)
        except socket.timeout: continue
        if a!=addr:
            print(f'[{ts()}] unexpected {a} {pkt[:20]!r}', flush=True); continue
        if pkt.startswith(DMRD):
            total+=1; d=parse_dmrd(pkt)
            if d['call_type']=='group' and d['dst']==args.tg:
                sel+=1; streams.setdefault(d['stream'], {'src':d['src'], 'frames':0}); streams[d['stream']]['frames']+=1
                print(f"[{ts()}] DMRD tg={d['dst']} src={d['src']} peer={d['peer']} slot={d['slot']} stream={d['stream']} ft={d['frame_type']} dv={d['dtype']} seq={d['seq']}", flush=True)
        elif pkt.startswith(DMRP):
            print(f'[{ts()}] DMRP pong/packet len={len(pkt)} {pkt!r}', flush=True)
        elif pkt.startswith(DMRB):
            print(f'[{ts()}] DMRB beacon request len={len(pkt)} {pkt!r}', flush=True)
        else:
            print(f'[{ts()}] other len={len(pkt)} {pkt[:32]!r}', flush=True)
    finally:
      sock.close()
    print(f'[{ts()}] done total_dmrd={total} tg{args.tg}_frames={sel} streams={len(streams)}', flush=True)
if __name__=='__main__': main()
