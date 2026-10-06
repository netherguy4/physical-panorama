#!/usr/bin/env python3
"""Run with python3 tests/test_panorama.py; isolated temporary state, no desktop changes."""
import copy
import contextlib
import fcntl
import io
import json
import os
import shutil
import sys
import threading
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import physical_panorama as panorama
from PIL import Image
from physical_panorama import parse_layout,physical_layout,framing,source_box

text=Path(__file__).with_name('layout.kdl').read_text()
outputs=parse_layout(text)
layout=physical_layout(outputs,{})
assert layout['canvas_logical']==[6016,1440]
assert layout['canvas_mm']==[1440,340]
byname={o['name']:o for o in outputs}
left,center,right=(byname[x] for x in ('HDMI-A-1','DP-1','eDP-1'))
assert left['physical_position'][0]+left['mm'][0]==center['physical_position'][0]
assert center['physical_position'][0]+center['mm'][0]==right['physical_position'][0]
assert abs(right['physical_position'][1]+right['mm'][1]-center['physical_position'][1]-center['mm'][1])<1e-9
aligned_top=physical_layout(parse_layout(text.replace('position 4480 480','position 4480 0')), {})
top_byname={o['name']:o for o in aligned_top['outputs']}
assert top_byname['eDP-1']['physical_position'][1]==top_byname['DP-1']['physical_position'][1]
frame=framing(dict(width=14400,height=3400),layout,{})
assert frame['valid'] and frame['density']==10
for a,b in ((left,center),(center,right)):
 assert source_box(a,frame)[2]==source_box(b,frame)[0]
 # A shared physical seam point maps to exactly the same source Y on either output.
 y=max(a['physical_position'][1],b['physical_position'][1])+10
 ay=source_box(a,frame)[1]+(y-a['physical_position'][1])*frame['density']
 by=source_box(b,frame)[1]+(y-b['physical_position'][1])*frame['density']
 assert abs(ay-by)<1e-9
assert not framing(dict(width=1920,height=1080),layout,{})['valid']
assert not framing(dict(width=9000,height=9000),layout,{})['valid']
rotated=parse_layout(text.replace('transform "normal"','transform "90"'))
rotated_left=next(o for o in rotated if o['name']=='HDMI-A-1')
assert rotated_left['pixels']==[1080,1920] and rotated_left['mm']==[310,540]
stacked=copy.deepcopy(parse_layout(text))
for index,o in enumerate(stacked):o['position']=[-100,index*1500-400]
changed=physical_layout(stacked,{})
assert changed['canvas_mm']!=layout['canvas_mm']
assert all(min(o['physical_position'])>=0 for o in changed['outputs'])
assert len(parse_layout(text.replace('"eDP-1" enabled=#true','"eDP-1" enabled=#false')))==2
missing=copy.deepcopy(parse_layout(text));missing[0]['mm']=[0,0]
assert physical_layout(missing,{})['outputs'][0]['size_fallback']
calibrated=physical_layout(parse_layout(text),{'monitors':{'HDMI-A-1':{'offset_mm':[0,5]}}})
calibrated_left=next(o for o in calibrated['outputs'] if o['name']=='HDMI-A-1')
assert abs(calibrated_left['physical_position'][1]-left['physical_position'][1]-5)<1e-9
edid=bytearray(128);edid[:8]=bytes.fromhex('00ffffffffffff00')
block=bytearray(18);block[0]=1;block[2]=1920&255;block[4]=(1920>>8)<<4;block[5]=1080&255;block[7]=(1080>>8)<<4
block[12]=543&255;block[13]=302&255;block[14]=((543>>8)<<4)|(302>>8)
edid[54:72]=block;edid[-1]=(-sum(edid[:-1]))%256
assert panorama.edid_size(edid,[1920,1080])==[543,302]
assert panorama.edid_size(edid,[1280,720])==[543,302]
assert panorama.edid_size(bytes(128),[1920,1080]) is None
corrupt=bytearray(edid);corrupt[20]^=1
assert panorama.edid_size(corrupt,[1920,1080]) is None
with tempfile.TemporaryDirectory() as directory:
 connector=Path(directory)/'card1-HDMI-A-1';connector.mkdir();(connector/'edid').write_bytes(edid)
 refined=panorama.refine_sizes(parse_layout(text),Path(directory))
 assert next(o for o in refined if o['name']=='HDMI-A-1')['mm']==[543,302]
 refined=panorama.refine_sizes(parse_layout(text.replace('transform "normal"','transform "90"')),Path(directory))
 assert next(o for o in refined if o['name']=='HDMI-A-1')['mm']==[302,543]
physical_settings={'monitors':{'DP-1':{'position_mm':[0,0]},'HDMI-A-1':{'position_mm':[-560,30]},'eDP-1':{'position_mm':[625,150]}}}
measured=physical_layout(parse_layout(text),physical_settings)
assert measured['canvas_mm']==[1485,340]
virtual_moved=parse_layout(text.replace('position 0 230','position 0 500').replace('position 4480 480','position 4480 100'))
remeasured=physical_layout(virtual_moved,physical_settings)
assert [o['physical_position'] for o in measured['outputs']]==[o['physical_position'] for o in remeasured['outputs']]
byname={o['name']:o for o in measured['outputs']}
assert byname['DP-1']['physical_position'][0]-byname['HDMI-A-1']['physical_position'][0]-byname['HDMI-A-1']['mm'][0]==20
assert byname['eDP-1']['physical_position'][0]-byname['DP-1']['physical_position'][0]-byname['DP-1']['mm'][0]==25
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary)
 panorama.STATE=root/'state';panorama.STATE.mkdir()
 panorama.CONFIG_HOME=root/'xdg-config'
 panorama.COSMIC=root/'config';panorama.COSMIC.mkdir()
 panorama.UNITS=root/'units'
 gallery=panorama.CONFIG_HOME/'cosmic/com.system76.CosmicSettings.Wallpaper/v1/custom-images'
 gallery.parent.mkdir(parents=True)
 custom=str(root/'Обои, ] "quoted" \\image.jpg')
 gallery.write_text('[\n    '+json.dumps(custom,ensure_ascii=False)+',\n]\n')
 (panorama.COSMIC/'all').write_text('original wallpaper')
 for name in ('first','second'):
  generation=root/name;generation.mkdir()
  for output in layout['outputs']:(generation/(output['name']+'.jpg')).write_text(name)
  assert panorama.apply(layout,generation)
  assert all((panorama.STATE/'current'/(o['name']+'.jpg')).read_text()==name for o in layout['outputs'])
  assert (panorama.COSMIC/'same-on-all').read_text().strip()=='false'
  registered=json.loads(gallery.read_text())
  assert registered==[custom]+[str(panorama.STATE/'current'/(o['name']+'.jpg')) for o in layout['outputs']]
  assert all(Path(image).is_file() for image in registered[1:])
 assert (panorama.STATE/'backup/all').read_text()=='original wallpaper'
 # Re-applying an unchanged generation must not trigger another COSMIC reload.
 assert not panorama.apply(layout,generation)
 # Settings uses native RON formatting and may reorder the same connector set.
 native='[\n'+''.join('    '+json.dumps(o['name'])+',\n' for o in reversed(layout['outputs']))+']\n'
 (panorama.COSMIC/'backgrounds').write_text(native)
 for output in layout['outputs']:
  entry=panorama.COSMIC/('output.'+output['name'])
  entry.write_text(entry.read_text().rstrip())
 assert 'scaling_mode: Zoom,' in (panorama.COSMIC/'output.DP-1').read_text()
 assert not panorama.apply(layout,generation)
 assert (panorama.COSMIC/'backgrounds').read_text()==native
 before=(panorama.COSMIC/'backgrounds').stat().st_mtime_ns
 gallery.write_text('[]')
 assert not panorama.apply(layout,generation)
 assert (panorama.COSMIC/'backgrounds').stat().st_mtime_ns==before
 assert json.loads(gallery.read_text())==registered[1:]
 # Native RON escaping must survive a round trip without changing unrelated image paths.
 gallery.write_text(r'''["/pictures/it\'s.jpg", "/pictures/\u{202e}space.jpg", "/pictures/literal\\u{41}.jpg", "/pictures/\u{8}control.jpg",]''')
 assert not panorama.apply(layout,generation)
 normalized=gallery.read_text()
 assert "it's.jpg" in normalized and '\u202espace.jpg' in normalized
 assert r'literal\\u{41}.jpg' in normalized and r'\u{8}control.jpg' in normalized
 assert panorama.gallery_update([o['name'] for o in layout['outputs']]) is None
 gallery.write_text('not a path list')
 try: panorama.apply(layout,generation);raise AssertionError('invalid gallery accepted')
 except ValueError: pass
 assert gallery.read_text()=='not a path list'
 assert (panorama.COSMIC/'backgrounds').stat().st_mtime_ns==before
 gallery.write_text(json.dumps(registered,ensure_ascii=False))
 (panorama.COSMIC/'output.DP-1').write_text('changed in Settings')
 assert panorama.apply(layout,generation)
 small_layout=dict(canvas_mm=[100,50],outputs=[dict(name='test',pixels=[100,50],mm=[100,50],physical_position=[0,0])])
 for mode,color in [('RGB',(20,40,60)),('L',80)]:
  source=root/(mode+'.png');Image.new(mode,(200,100),color).save(source)
  row=dict(id=mode,file=source.name,path=str(source),width=200,height=100)
  destination=root/('render-'+mode)
  panorama.render(row,small_layout,{},destination)
  with Image.open(destination/'test.jpg') as result:
   assert result.size==(100,50) and result.mode=='RGB'
   assert max(abs(a-b) for a,b in zip(result.getpixel((50,25)),color if mode=='RGB' else (80,80,80)))<=2
 original_render=panorama.render
 panorama.prepare([row],small_layout,{})
 cached=panorama.generation_path(row,small_layout,{})
 assert panorama.ready(cached,small_layout)
 def no_render(*args): raise AssertionError('Prepared panorama was decoded again')
 panorama.render=no_render
 panorama.prepare([row],small_layout,{})
 changed_layout=copy.deepcopy(small_layout)
 changed_layout['outputs'][0]['pixels']=[200,100]
 assert panorama.generation_path(row,changed_layout,{})!=cached
 assert not panorama.ready(panorama.generation_path(row,changed_layout,{}),changed_layout)
 assert panorama.generation_path(dict(row,width=400,focus=[0.2,0.5]),small_layout,{})!=panorama.generation_path(dict(row,width=400),small_layout,{})
 assert panorama.generation_path(dict(row,source='1:2'),small_layout,{})!=panorama.generation_path(dict(row,source='1:3'),small_layout,{})
 (cached/'geometry.json').unlink()
 assert not panorama.ready(cached,small_layout)
 panorama.render=original_render
 # Folder mode: subfolders become groups, unreadable files are skipped.
 folder=root/'folder';(folder/'space').mkdir(parents=True)
 Image.new('RGB',(300,100)).save(folder/'wide.jpg');Image.new('RGB',(600,200)).save(folder/'space'/'nebula.png')
 (folder/'broken.jpg').write_text('not an image');(folder/'notes.txt').write_text('ignored')
 scanned={r['id']:r for r in panorama.catalog({'images':str(folder)})}
 assert set(scanned)=={'wide','space/nebula'}
 assert scanned['wide']['group']=='default' and scanned['space/nebula']['group']=='space'
 assert scanned['space/nebula']['width']==600 and Path(scanned['space/nebula']['path']).is_file()
 try: panorama.catalog({'images':str(root/'missing')});raise AssertionError('missing folder accepted')
 except ValueError: pass
 dirs=root/'user-dirs.dirs';dirs.write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
 saved_home=panorama.CONFIG_HOME;panorama.CONFIG_HOME=root
 assert panorama.pictures()==Path.home()/'Bilder'
 panorama.CONFIG_HOME=saved_home
 # Manifest mode keeps curated metadata (groups, focus, per-image limits).
 panorama.CONFIG=root/'settings.json';panorama.CONFIG.write_text(json.dumps({'group':'abstract','images':str(root)}))
 rows=[dict(id='current',title='Current',file='RGB.png',width=200,height=100,group='additional'),
       dict(id='other1',title='Other 1',file='L.png',width=200,height=100,group='abstract'),
       dict(id='other2',title='Other 2',file='RGB.png',width=200,height=100,group='abstract')]
 (root/'manifest.json').write_text(json.dumps(rows))
 rows=panorama.catalog({'images':str(root)})
 (panorama.STATE/'state.json').write_text(json.dumps(dict(panorama='current')))
 panorama.geometry=lambda settings:small_layout
 original_apply=panorama.apply
 events=[]
 barrier=None
 def traced_render(row,layout,settings,destination):
  events.append(('render',row['id'],threading.current_thread().name))
  if barrier is not None: barrier.wait(timeout=5)
  return original_render(row,layout,settings,destination)
 def traced_apply(layout,generation):
  events.append(('apply',json.loads((generation/'geometry.json').read_text())['panorama']))
  return original_apply(layout,generation)
 panorama.render=traced_render;panorama.apply=traced_apply
 saved_argv=sys.argv
 sys.argv=['panorama','reload'];panorama.main()
 assert [e[:2] for e in events]==[('render','current'),('apply','current'),('render','other1'),('render','other2')]
 assert json.loads((panorama.STATE/'state.json').read_text())['panorama']=='current'
 for other in rows[1:]:
  shutil.rmtree(panorama.generation_path(other,small_layout,{}))
 events.clear();barrier=threading.Barrier(2)
 sys.argv=['panorama','reload','--fast'];panorama.main()
 assert events[0]==('apply','current')
 assert {e[1] for e in events[1:]}=={'other1','other2'}
 assert len({e[2] for e in events[1:]})==2
 barrier=None;events.clear()
 # fit follows a stepwise hotplug until the layout is stable and never prepares other images.
 sequence=iter([small_layout,changed_layout,changed_layout,changed_layout])
 panorama.geometry=lambda settings:next(sequence)
 saved_sleep=panorama.time.sleep;panorama.time.sleep=lambda seconds:None
 sys.argv=['panorama','fit'];panorama.main()
 panorama.time.sleep=saved_sleep;panorama.geometry=lambda settings:small_layout
 assert [e[:2] for e in events]==[('apply','current'),('render','current'),('apply','current')]
 assert json.loads((panorama.STATE/'state.json').read_text())['layout']==changed_layout
 # fit crops the shown image even to a layout it is unsuitable for, instead of swapping it.
 original_framing=panorama.framing
 panorama.framing=lambda row,layout,settings:dict(original_framing(row,layout,settings),valid=row['id']!='current')
 panorama.time.sleep=lambda seconds:None
 panorama.geometry=lambda settings:small_layout
 sys.argv=['panorama','fit'];panorama.main()
 assert json.loads((panorama.STATE/'state.json').read_text())['panorama']=='current'
 panorama.framing=original_framing;panorama.time.sleep=saved_sleep
 # Other layouts' caches survive until unused for keep_days; incomplete ones go immediately.
 generations=panorama.STATE/'generations'
 for name in ('recent-layout','stale-layout'):
  (generations/name).mkdir();(generations/name/'geometry.json').write_text(json.dumps(dict(layout=dict(outputs=[]))))
 os.utime(generations/'stale-layout',(0,0));(generations/'partial.tmp').mkdir()
 panorama.prune(rows,small_layout,{})
 assert (generations/'recent-layout').exists() and not (generations/'stale-layout').exists() and not (generations/'partial.tmp').exists()
 assert panorama.ready(panorama.generation_path(rows[1],small_layout,{}),small_layout)
 assert (panorama.STATE/'current').resolve().exists()
 events.clear()
 original_run=panorama.subprocess.run
 panorama.subprocess.run=lambda command,**kwargs:events.append(('systemctl',command[2:]))
 sys.argv=['panorama','static'];panorama.main()
 units=panorama.units()
 assert all((panorama.UNITS/name).read_text()==body for name,body in units.items())
 assert sys.executable in units['physical-panorama.service'] and 'PathChanged=%S/cosmic-comp/outputs.ron' in units['physical-panorama-layout.path']
 assert '.py" fit\n' in units['physical-panorama-reload.service']
 assert events[0]==('systemctl',['daemon-reload'])
 assert ('systemctl',['disable','--now','physical-panorama.timer','physical-panorama-layout.path']) in events
 assert ('apply','current') in events
 assert events[-2:]==[('systemctl',['enable','physical-panorama-reload.service']),('systemctl',['enable','--now','physical-panorama-layout.path'])]
 events.clear();sys.argv=['panorama','slideshow'];panorama.main()
 # Units are unchanged, so no daemon-reload.
 assert events==[('systemctl',['disable','physical-panorama-reload.service']),('systemctl',['enable','--now','physical-panorama.timer','physical-panorama-layout.path'])]
 events.clear()
 added=str(root/'added-later.jpg')
 gallery.write_text(json.dumps(json.loads(gallery.read_text())+[added],ensure_ascii=False))
 sys.argv=['panorama','restore'];panorama.main()
 assert json.loads(gallery.read_text())==[custom,added]
 assert (panorama.COSMIC/'all').read_text()=='original wallpaper'
 # Applet operations use isolated files and systemctl replies, never the live session.
 running=[False]
 def systemctl_reply(command,**kwargs):
  events.append(('systemctl',command[2:]))
  return panorama.subprocess.CompletedProcess(command,0 if command[2]!='is-active' or running[0] else 3)
 panorama.subprocess.run=systemctl_reply
 def call(*arguments):
  sys.argv=['panorama',*arguments]
  with contextlib.redirect_stdout(io.StringIO()) as captured: panorama.main()
  return captured.getvalue()
 with (panorama.STATE/'lock').open('w') as held:
  fcntl.flock(held,fcntl.LOCK_EX)
  status=json.loads(call('status','--json'))
 assert not status['active'] and not status['slideshow'] and status['interval']==15
 assert status['group']=='abstract' and status['eligible']==2
 assert {group['id']:group['count'] for group in status['groups']}=={'all':3,'abstract':2,'additional':1}
 settings=json.loads(panorama.CONFIG.read_text());settings['custom']={'preserve':True}
 panorama.CONFIG.write_text(json.dumps(settings))
 before=panorama.CONFIG.read_text()
 for group in ('typo',''):
  try: call('configure','--group',group);raise AssertionError('unknown group accepted')
  except ValueError: pass
  assert panorama.CONFIG.read_text()==before
 call('configure','--group','all','--interval','30')
 assert json.loads(panorama.CONFIG.read_text())['custom']=={'preserve':True}
 assert 'OnUnitActiveSec=30min' in (panorama.UNITS/'physical-panorama.timer').read_text()
 assert json.loads(call('status','--json'))['active']
 call('next','--id','other1');call('next','--id','other2')
 assert json.loads(call('status','--json'))['can_previous']
 call('previous')
 assert json.loads((panorama.STATE/'state.json').read_text())['panorama']=='other1'
 # Missing/deleted historical IDs are skipped; previous never re-appends the popped entry.
 state=json.loads((panorama.STATE/'state.json').read_text())
 state['history']=['current','deleted','other1'];(panorama.STATE/'state.json').write_text(json.dumps(state))
 call('previous')
 state=json.loads((panorama.STATE/'state.json').read_text())
 assert state['panorama']=='current' and state['history']==['current']
 assert not json.loads(call('status','--json'))['can_previous']
 try: call('previous');raise AssertionError('empty history accepted')
 except ValueError: pass
 shutil.rmtree(panorama.generation_path(rows[1],small_layout,{}))
 call('next','--id','other1')
 assert panorama.ready(panorama.generation_path(rows[1],small_layout,{}),small_layout)
 running[0]=True;events.clear()
 call('configure','--interval','60')
 assert ('systemctl',['restart','physical-panorama.timer']) in events
 assert 'OnActiveSec=60min' in (panorama.UNITS/'physical-panorama.timer').read_text()
 assert json.loads(call('status','--json'))['slideshow']
 events.clear();call('slideshow','--interval','5')
 assert ('systemctl',['restart','physical-panorama.timer']) in events
 assert 'OnUnitActiveSec=5min' in (panorama.UNITS/'physical-panorama.timer').read_text()
 events.clear();call('slideshow','--interval','5')
 assert ('systemctl',['restart','physical-panorama.timer']) not in events
 # A timer restart may fire from the service's old timestamp: the scheduled command must skip it.
 before=(panorama.STATE/'state.json').read_text()
 assert 'keeping the current panorama' in call('next','--scheduled')
 assert (panorama.STATE/'state.json').read_text()==before
 saved_time=panorama.time.time
 latest=max(json.loads(before)['updated'],panorama.CONFIG.stat().st_mtime)
 panorama.time.time=lambda:latest+5*60+1
 call('next','--scheduled')
 assert (panorama.STATE/'state.json').read_text()!=before
 panorama.time.time=saved_time
 for interval in ('0','1441','oops'):
  before=panorama.CONFIG.read_text()
  with contextlib.redirect_stderr(io.StringIO()):
   try: call('configure','--interval',interval);raise AssertionError('invalid interval accepted')
   except SystemExit as error: assert error.code==2
  assert panorama.CONFIG.read_text()==before
 panorama.subprocess.run=original_run;sys.argv=saved_argv
print('PASS: native modes, fractional scale, offsets, unequal physical sizes, continuous seams, rotation, stacking, disabled outputs, EDID, calibration, suitability, folder/manifest catalogs, units, idempotent apply, gallery registration/restore, native RON escapes, stepwise hotplug, multi-layout cache')
