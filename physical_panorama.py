#!/usr/bin/env python3
"""Span one panorama across several monitors at true physical scale (COSMIC). No resident process."""
import argparse
import ast
import fcntl
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image

APP = 'physical-panorama'
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state') / APP
CONFIG = CONFIG_HOME / APP / 'config.json'
COSMIC = CONFIG_HOME / 'cosmic/com.system76.CosmicBackground/v1'
UNITS = CONFIG_HOME / 'systemd/user'
SUFFIXES = {'.jpg','.jpeg','.png','.webp','.tif','.tiff','.avif'}
Image.MAX_IMAGE_PIXELS = 300_000_000


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text)
    temp.replace(path)


def pictures():
    dirs = CONFIG_HOME/'user-dirs.dirs'
    found = re.search(r'^XDG_PICTURES_DIR="(.+)"$', dirs.read_text(), re.M) if dirs.exists() else None
    return Path(found[1].replace('$HOME',str(Path.home()))) if found else Path.home()/'Pictures'


def catalog(settings):
    root = Path(settings['images']).expanduser() if settings.get('images') else pictures()/'Panoramas'
    if not root.is_dir(): raise ValueError(f'Image folder {root} does not exist; create it or set "images" in {CONFIG}')
    manifest = root/'manifest.json'
    if manifest.exists():
        rows = json.loads(manifest.read_text())
        for row in rows: row['path'] = str(root/row['file'])
        return rows
    rows = []
    for path in sorted(root.rglob('*')):
        if path.suffix.lower() not in SUFFIXES or not path.is_file(): continue
        try:
            with Image.open(path) as image: width,height = image.size
        except (OSError, Image.DecompressionBombError):
            continue
        relative, stat = path.relative_to(root), path.stat()
        # Size and mtime stand in for a content hash; hashing gigabytes on every run is too slow.
        rows.append(dict(id=relative.with_suffix('').as_posix(), title=path.stem, file=relative.as_posix(), path=str(path),
                         width=width, height=height, group=relative.parts[0] if len(relative.parts)>1 else 'default',
                         source=f'{stat.st_size}:{stat.st_mtime_ns}'))
    return rows


def parse_layout(text):
    outputs = []
    for match in re.finditer(r'^output "([^"\n]+)" enabled=#true \{\n(.*?)^\}', text, re.M | re.S):
        name, block = match.groups()
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Unsafe connector name')
        def field(key):
            found = re.search(rf'^  {key} (.+)$', block, re.M)
            if not found: raise ValueError(f'{name}: missing {key}')
            return found[1]
        mode = re.search(r'^    mode (\d+) (\d+) (\d+).*current=#true', block, re.M)
        if not mode: raise ValueError(f'{name}: missing current mode')
        width, height, refresh = map(int, mode.groups())
        transform = field('transform').strip('"')
        if transform not in ('normal','90','180','270','flipped','flipped-90','flipped-180','flipped-270'):
            raise ValueError(f'Unknown transform {transform}')
        mm = list(map(float,field('physical').split()))
        if transform in ('90','270','flipped-90','flipped-270'):
            width,height = height,width
            mm.reverse()
        scale = float(field('scale'))
        if scale <= 0 or width <= 0 or height <= 0: raise ValueError('Invalid geometry')
        outputs.append(dict(name=name, pixels=[width,height], mode=list(map(int,mode.groups())),
                            logical=[width/scale,height/scale], position=list(map(float,field('position').split())),
                            mm=mm, scale=scale, transform=transform, primary='xwayland_primary #true' in block))
    if not outputs: raise ValueError('No active outputs; refusing to change wallpaper')
    return sorted(outputs,key=lambda o:o['name'])


def physical_layout(outputs, settings):
    reference = next((o for o in outputs if o['primary']), outputs[0])
    calibration = settings.get('monitors',{})
    for o in outputs:
        overrides = calibration.get(o['name'],{})
        if 'size_mm' in overrides: o['mm'] = overrides['size_mm']
        if min(o['mm']) <= 0:
            o['mm'] = [n*25.4/96 for n in o['logical']]
            o['size_fallback'] = '96 logical DPI; set size_mm to calibrate'
        if len(o['mm']) != 2 or min(o['mm']) <= 0: raise ValueError('Invalid physical dimensions')
    reference['physical_position'] = [0.,0.]
    placed = [reference]
    pending = [o for o in outputs if o is not reference]
    while pending:
        def distance(pair):
            a,b = pair
            gaps = [max(a['position'][i]-b['position'][i]-b['logical'][i],
                        b['position'][i]-a['position'][i]-a['logical'][i],0) for i in range(2)]
            centers = sum((a['position'][i]+a['logical'][i]/2-b['position'][i]-b['logical'][i]/2)**2 for i in range(2))
            return (sum(g*g for g in gaps),centers)
        a,b = min(((a,b) for a in placed for b in pending), key=distance)
        position = []
        for i in range(2):
            al,bl = a['position'][i],b['position'][i]
            ar,br = al+a['logical'][i],bl+b['logical'][i]
            if bl >= ar:
                value = a['physical_position'][i]+a['mm'][i]+(bl-ar)*a['mm'][i]/a['logical'][i]
            elif br <= al:
                value = a['physical_position'][i]-b['mm'][i]-(al-br)*a['mm'][i]/a['logical'][i]
            else:
                # Preserve aligned edges in mm; overlap midpoints drift with unequal physical sizes.
                top=math.isclose(al,bl,rel_tol=0,abs_tol=1)
                bottom=math.isclose(ar,br,rel_tol=0,abs_tol=1)
                if top and not bottom: anchor=max(al,bl)
                elif bottom and not top: anchor=min(ar,br)
                else: anchor=(max(al,bl)+min(ar,br))/2
                value = (a['physical_position'][i]+(anchor-al)*a['mm'][i]/a['logical'][i]
                         -(anchor-bl)*b['mm'][i]/b['logical'][i])
            position.append(value)
        b['physical_position'] = position
        placed.append(b)
        pending.remove(b)
    for o in outputs:
        overrides=calibration.get(o['name'],{})
        if 'position_mm' in overrides:
            position=overrides['position_mm']
            if len(position)!=2 or not all(math.isfinite(v) for v in position): raise ValueError('Invalid position_mm')
            o['physical_position']=list(position)
            o['position_source']='measured physical mm'
        delta = overrides.get('offset_mm',[0,0])
        if len(delta)!=2 or not all(math.isfinite(v) for v in delta): raise ValueError('Invalid offset_mm')
        o['physical_position'] = [o['physical_position'][i]+delta[i] for i in range(2)]
    origin = [min(o['physical_position'][i] for o in outputs) for i in range(2)]
    for o in outputs:
        o['physical_position'] = [o['physical_position'][i]-origin[i] for i in range(2)]
    canvas = [max(o['physical_position'][i]+o['mm'][i] for o in outputs) for i in range(2)]
    return dict(outputs=outputs,canvas_mm=canvas,
                canvas_logical=[max(o['position'][i]+o['logical'][i] for o in outputs)-min(o['position'][i] for o in outputs) for i in range(2)])


def edid_size(data,native):
    # ponytail: base-block timings only; add DisplayID parsing if COSMIC fallback sizes prove insufficient.
    if len(data)<128 or data[:8]!=bytes.fromhex('00ffffffffffff00') or sum(data[:128])%256: return None
    sizes=[]
    for offset in (54,72,90,108):
        block=data[offset:offset+18]
        if not int.from_bytes(block[:2],'little'): continue
        pixels=[block[2]+((block[4]>>4)<<8),block[5]+((block[7]>>4)<<8)]
        mm=[block[12]+((block[14]>>4)<<8),block[13]+((block[14]&15)<<8)]
        if min(mm)>0:
            sizes.append((pixels,mm))
    return next((mm for pixels,mm in sizes if pixels==native),sizes[0][1] if sizes else None)


def refine_sizes(outputs,drm=Path('/sys/class/drm')):
    for output in outputs:
        candidates=[]
        for path in drm.glob('card*-'+output['name']+'/edid'):
            try:
                size=edid_size(path.read_bytes(),output['mode'][:2])
            except OSError:
                continue
            if size is not None: candidates.append(size)
        if candidates and all(size==candidates[0] for size in candidates):
            output['mm']=list(reversed(candidates[0])) if output['transform'] in ('90','270','flipped-90','flipped-270') else candidates[0]
            output['size_source']='EDID detailed timing'
    return outputs


def geometry(settings):
    randr = shutil.which('cosmic-randr')
    if not randr: raise ValueError('cosmic-randr not found; only the COSMIC desktop is supported')
    text = subprocess.check_output([randr,'list','--kdl'], text=True, timeout=10)
    return physical_layout(refine_sizes(parse_layout(text)),settings)


def framing(row, layout, settings):
    cw,ch = layout['canvas_mm']
    w,h = row['width'],row['height']
    density = min(w/cw,h/ch)
    native = max(max(o['pixels'][i]/o['mm'][i] for i in range(2)) for o in layout['outputs'])
    retained = cw*ch*density*density/(w*h)
    valid = retained >= row.get('min_retained',settings.get('min_retained',0.65)) and native/density <= settings.get('max_resample',1.25)
    center = row.get('focus',[0.5,0.5])
    offset = [max(0,min(w-cw*density,w*center[0]-cw*density/2)),
              max(0,min(h-ch*density,h*center[1]-ch*density/2))]
    return dict(valid=valid,density=density,offset=offset,retained=retained,resample=native/density)


def source_box(output, frame):
    return tuple(frame['offset'][i%2]+(output['physical_position'][i%2]+(output['mm'][i%2] if i>=2 else 0))*frame['density'] for i in range(4))


def render(row,layout,settings,destination):
    destination.mkdir(parents=True,exist_ok=True)
    frame = framing(row,layout,settings)
    with Image.open(row['path']) as image:
        if image.size != (row['width'],row['height']): raise ValueError(f"{row['file']}: dimensions differ from catalog")
        if image.mode != 'RGB':
            image = image.convert('RGB')
        for output in layout['outputs']:
            # Floating boxes sample one shared image coordinate system, avoiding per-monitor rounding drift.
            crop = image.resize(tuple(output['pixels']),Image.Resampling.LANCZOS,box=source_box(output,frame))
            crop.save(destination/(output['name']+'.jpg'),quality=98,subsampling=0)
    atomic(destination/'geometry.json',json.dumps(dict(panorama=row['id'],layout=layout,framing=frame),indent=2))
    return frame


def generation_path(row,layout,settings):
    signature=hashlib.sha256(json.dumps(dict(version=4,id=row['id'],source=row.get('sha256') or row.get('source'),
        layout=layout,framing=framing(row,layout,settings)),sort_keys=True).encode()).hexdigest()[:24]
    return STATE/'generations'/signature


def ready(generation,layout):
    return (generation/'geometry.json').is_file() and all(
        (generation/(o['name']+'.jpg')).is_file() for o in layout['outputs'])


def prepare(rows,layout,settings,fast=False):
    rows=[r for r in rows if not ready(generation_path(r,layout,settings),layout)]
    def one(row):
        generation=generation_path(row,layout,settings)
        if ready(generation,layout): return
        if shutil.disk_usage(STATE).free < 200*1024*1024:
            raise ValueError('Insufficient free space for panorama cache')
        temporary=generation.with_name(generation.name+'.tmp')
        try:
            render(row,layout,settings,temporary)
            if generation.exists(): shutil.rmtree(generation)
            temporary.replace(generation)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
        print(f"Prepared {row['id']}",flush=True)

    if fast and len(rows)>1:
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        estimate=max(r['width']*r['height']*4 for r in rows)+sum(o['pixels'][0]*o['pixels'][1]*8 for o in layout['outputs'])+64*1024**2
        workers=min(4,os.cpu_count() or 1,max(1,available//(estimate*2)))
        print(f'Preparing cache with {workers} workers',flush=True)
        with ThreadPoolExecutor(max_workers=workers) as pool: list(pool.map(one,rows))
    else:
        for row in rows: one(row)


def prune(rows,layout,settings,keep_days=30):
    protected={generation_path(r,layout,settings) for r in rows if framing(r,layout,settings)['valid']}
    for path in protected:
        if path.exists(): os.utime(path)
    current=(STATE/'current').resolve() if (STATE/'current').is_symlink() else None
    cutoff=time.time()-keep_days*86400
    # Other layouts (docked/undocked, lid closed) keep their cache until unused for keep_days.
    for old in (STATE/'generations').iterdir():
        if not old.is_dir() or old in protected or old==current: continue
        try: same=json.loads((old/'geometry.json').read_text())['layout']==layout
        except (OSError,ValueError,KeyError): same=True
        if same or old.stat().st_mtime<cutoff: shutil.rmtree(old)


def gallery_update(names):
    path = CONFIG_HOME/'cosmic/com.system76.CosmicSettings.Wallpaper/v1/custom-images'
    text = path.read_text() if path.exists() else '[]'
    # Native RON string lists allow trailing commas, escaped apostrophes and Rust Unicode escapes.
    text = re.sub(r'\\(?:\\|u\{([0-9a-fA-F]+)\})',
                  lambda m: '\\U'+f'{int(m[1],16):08x}' if m[1] else m[0], text)
    try: images = ast.literal_eval(text)
    except (SyntaxError,ValueError) as error:
        raise ValueError(f'Cannot read {path}; custom images left unchanged') from error
    if not isinstance(images,list) or not all(isinstance(image,str) for image in images):
        raise ValueError(f'Expected an image-path list in {path}; custom images left unchanged')
    current = STATE/'current'
    updated = [image for image in images if Path(image).parent!=current or Path(image).suffix!='.jpg']
    updated.extend(str(current/(name+'.jpg')) for name in names)
    if updated==images: return None
    text = json.dumps(updated,ensure_ascii=False,indent=4)+'\n'
    text = re.sub(r'\\(?:\\|([bf])|u([0-9a-fA-F]{4}))',
                  lambda m: '\\u{'+(m[2] or ('8' if m[1]=='b' else 'c'))+'}' if m[1] or m[2] else m[0], text)
    return path,text


def apply(layout,generation):
    names = [o['name'] for o in layout['outputs']]
    gallery = gallery_update(names)
    # Native-size crops need no scaling; Zoom and native formatting survive Settings' normalization.
    values = {('output.'+name): (f'(\n    output: {json.dumps(name)},\n'
              f'    source: Path({json.dumps(str(STATE/"current"/(name+".jpg")),ensure_ascii=False)}),\n'
              '    filter_by_theme: false,\n    rotation_frequency: 0,\n    filter_method: Lanczos,\n'
              '    scaling_mode: Zoom,\n    sampling_method: Alphanumeric,\n)\n') for name in names}
    values['same-on-all'] = 'false\n'
    values['backgrounds'] = json.dumps(names)+'\n'
    backgrounds = COSMIC/'backgrounds'
    if backgrounds.is_file():
        text = backgrounds.read_text()
        try: existing = ast.literal_eval(text)
        except (SyntaxError,ValueError): existing = None
        if isinstance(existing,list) and all(isinstance(name,str) for name in existing) and len(existing)==len(names) and set(existing)==set(names):
            values['backgrounds'] = text
    current = STATE/'current'
    if current.is_symlink() and current.resolve()==generation.resolve() and all(
            (COSMIC/key).is_file() and (COSMIC/key).read_text().strip()==value.strip() for key,value in values.items()):
        if gallery is not None: atomic(*gallery)
        return False
    COSMIC.mkdir(parents=True,exist_ok=True)
    if not (STATE/'backup').exists():
        shutil.copytree(COSMIC,STATE/'backup')
    link = STATE/'current.new'
    if link.is_symlink(): link.unlink()
    link.symlink_to(generation.resolve(), target_is_directory=True)
    link.replace(current)
    os.utime(generation)
    # Settings reapplies its selection on entry; every crop must be discoverable in its gallery.
    if gallery is not None: atomic(*gallery)
    for key,value in values.items():
        if key!='backgrounds' and not ((COSMIC/key).is_file() and (COSMIC/key).read_text().strip()==value.strip()): atomic(COSMIC/key,value)
    # The symlink publishes the complete generation first; one config event reloads every output together.
    atomic(COSMIC/'backgrounds',values['backgrounds'])
    return True


def units(settings=None):
    interval = (settings or {}).get('interval',15)
    command = ' '.join('"'+str(part).replace('%','%%')+'"' for part in (sys.executable, Path(__file__).resolve()))
    session = 'After=cosmic-session.target\nPartOf=cosmic-session.target\n'
    oneshot = '[Service]\nType=oneshot\nNice=15\nIOSchedulingClass=idle\nTimeoutStartSec=900\n'
    return {
        f'{APP}.service': f'[Unit]\nDescription=Switch to a random panorama\n{session}\n{oneshot}'
                          f'ExecStartPre={command} prepare\nExecStart={command} next --scheduled\n',
        # Interval edits restart the timer; use the full interval for its first trigger too.
        f'{APP}.timer': '[Unit]\nDescription=Panorama slideshow\nPartOf=cosmic-session.target\n\n'
                        f'[Timer]\nOnActiveSec={interval}min\nOnUnitActiveSec={interval}min\nAccuracySec=15s\nRandomizedDelaySec=15s\n\n[Install]\nWantedBy=cosmic-session.target\n',
        # No default dependencies: WantedBy plus After on the same target would form an ordering cycle.
        f'{APP}-reload.service': f'[Unit]\nDescription=Re-fit the current panorama to the monitor layout\nDefaultDependencies=no\n{session}\n'
                                 f'{oneshot}ExecStartPre=/usr/bin/sleep 2\nExecStart={command} fit\n\n[Install]\nWantedBy=cosmic-session.target\n',
        # cosmic-comp rewrites outputs.ron whenever monitors are connected or rearranged.
        f'{APP}-layout.path': f'[Unit]\nDescription=Watch the COSMIC monitor layout\nPartOf=cosmic-session.target\n\n'
                              f'[Path]\nPathChanged=%S/cosmic-comp/outputs.ron\nUnit={APP}-reload.service\n\n[Install]\nWantedBy=cosmic-session.target\n',
    }


def systemctl(*args):
    subprocess.run(['systemctl','--user',*args],check=True,timeout=15)


def install_units(settings=None):
    changed = False
    for name,text in units(settings).items():
        path = UNITS/name
        if not path.exists() or path.read_text()!=text:
            atomic(path,text); changed = True
    if changed: systemctl('daemon-reload')


def timer_active():
    return subprocess.run(['systemctl','--user','is-active','--quiet',f'{APP}.timer'],
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3).returncode==0


def save_settings(settings):
    previous = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    running = settings.get('interval',15)!=previous.get('interval',15) and timer_active()
    atomic(CONFIG,json.dumps(settings,indent=2)+'\n')
    if (UNITS/f'{APP}.timer').exists():
        install_units(settings)
        if running: systemctl('restart',f'{APP}.timer')


def history_for(state):
    history = state.get('history',[])[:]
    current = state.get('panorama')
    if current and (not history or history[-1]!=current): history.append(current)
    return history


def applet_status(settings):
    state = json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else {}
    layout = state.get('layout') or geometry(settings)
    rows = catalog(settings)
    eligible = [row for row in rows if framing(row,layout,settings)['valid']]
    groups = ['all']+sorted({row.get('group','default') for row in rows}-{'all'})
    group = settings.get('group','all')
    selected = {row['id'] for row in eligible if group=='all' or row.get('group','default')==group}
    outputs = layout['outputs']
    active = bool(outputs) and (STATE/'current').is_symlink() and all(
        (COSMIC/('output.'+output['name'])).is_file() and
        json.dumps(str(STATE/'current'/(output['name']+'.jpg')),ensure_ascii=False) in
        (COSMIC/('output.'+output['name'])).read_text() for output in outputs)
    return dict(panorama=state.get('panorama'),title=state.get('title'),active=active,
                slideshow=timer_active(),interval=settings.get('interval',15),group=group,
                groups=[dict(id=name,count=sum(name=='all' or row.get('group','default')==name for row in eligible)) for name in groups],
                eligible=len(selected),can_previous=any(item in selected and item!=state.get('panorama') for item in history_for(state)[:-1]))


def main():
    parser = argparse.ArgumentParser(prog=APP, description=__doc__)
    parser.add_argument('command',nargs='?',default='next',choices=['next','previous','configure','fit','reload','layout','status','list','render','restore','prepare','static','slideshow'])
    parser.add_argument('--id',help='Select an image by catalog ID (see list)')
    parser.add_argument('--group',help='Override the configured group for this run ("all" for every image)')
    parser.add_argument('--destination',type=Path,help='Offline render directory; does not apply')
    parser.add_argument('--fast',action='store_true',help='Prepare cache in up to four memory-limited worker threads')
    parser.add_argument('--json',action='store_true',help='Machine-readable applet status')
    parser.add_argument('--interval',type=int,help='Slideshow interval in minutes (1–1440), saved to configuration')
    parser.add_argument('--scheduled',action='store_true',help='Timer invocation: skip changes before the configured interval')
    args = parser.parse_args()
    if args.fast and args.command not in ('reload','prepare'): parser.error('--fast requires reload or prepare')
    if args.json and args.command!='status': parser.error('--json requires status')
    if args.scheduled and args.command!='next': parser.error('--scheduled requires next')
    if args.interval is not None and (args.command not in ('configure','slideshow') or not 1<=args.interval<=1440):
        parser.error('--interval requires configure or slideshow and a value from 1 to 1440')
    if args.command=='configure' and args.group is None and args.interval is None:
        parser.error('configure requires --group or --interval')
    settings = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if args.command=='status':
        # State is atomically replaced; taking the render lock here would freeze the applet during preparation.
        print(json.dumps(applet_status(settings),ensure_ascii=False) if args.json else
              (STATE/'state.json').read_text() if (STATE/'state.json').exists() else 'No panorama applied yet')
        return
    STATE.mkdir(parents=True,exist_ok=True)
    if args.command=='static': install_units(settings)
    if args.command in ('restore','static'):
        if (UNITS/f'{APP}.timer').exists():
            systemctl('disable','--now',f'{APP}.timer',f'{APP}-layout.path')
            systemctl('stop',f'{APP}.service')
            systemctl('disable',f'{APP}-reload.service')
    with (STATE/'lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        settings = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        if args.scheduled:
            state = json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else {}
            # Restarting a timer preserves the service's old activation time and can fire it immediately.
            latest = max(state.get('updated',0),CONFIG.stat().st_mtime if CONFIG.exists() else 0)
            if time.time()-latest < settings.get('interval',15)*60:
                print('Slideshow interval has not elapsed; keeping the current panorama'); return
        old_interval = settings.get('interval',15)
        if args.interval is not None: settings['interval']=args.interval
        if args.command=='slideshow':
            restart = settings.get('interval',15)!=old_interval and timer_active()
            if args.interval is not None: atomic(CONFIG,json.dumps(settings,indent=2)+'\n')
            install_units(settings)
            systemctl('disable',f'{APP}-reload.service')
            systemctl('enable','--now',f'{APP}.timer',f'{APP}-layout.path')
            if restart: systemctl('restart',f'{APP}.timer')
            print(f"Slideshow enabled: random panorama approximately every {settings.get('interval',15)} minutes"); return
        if args.command=='configure' and args.group is None:
            save_settings(settings)
            print(f"Slideshow interval: {settings['interval']} minutes"); return
        if args.command=='restore':
            if not (STATE/'backup').is_dir(): raise ValueError('No wallpaper backup')
            gallery = gallery_update([])
            backup_names = {p.name for p in (STATE/'backup').iterdir()}
            for path in COSMIC.iterdir():
                if (path.name.startswith('output.') or path.name in ('backgrounds','same-on-all')) and path.name not in backup_names:
                    path.unlink()
            for path in (STATE/'backup').iterdir(): atomic(COSMIC/path.name,path.read_text())
            if gallery is not None: atomic(*gallery)
            print('Automatic changes disabled; previous COSMIC wallpaper configuration restored')
            return
        # Hotplug happens in steps (one output, then the rest); re-fit until the layout stops changing.
        for _ in range(10):
            layout = geometry(settings)
            if args.command=='layout':
                print(json.dumps(layout,indent=2)); return
            rows = catalog(settings)
            group = args.group if args.group is not None else settings.get('group','all')
            if args.command=='configure': settings['group']=group
            grouped = [r for r in rows if group=='all' or r.get('group','default')==group]
            eligible = [r for r in grouped if framing(r,layout,settings)['valid']]
            if args.command=='list':
                for r in grouped:
                    frame = framing(r,layout,settings)
                    print(f"{'ok ' if frame['valid'] else 'no '} {r['id']}  [{r.get('group','default')}]  keeps {frame['retained']:.0%}, resample {frame['resample']:.2f}x")
                return
            unsuitable = (f'No image in group "{group}" fits this layout ({len(rows)} found); '
                          f'panoramas must be wide and large enough, see "{APP} list"')
            if args.command=='prepare':
                if not eligible: raise ValueError(unsuitable)
                prepare(eligible,layout,settings,fast=args.fast)
                prune(rows,layout,settings)
                print(f'Cache ready: {len(eligible)} suitable panoramas'); return
            state = json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else {}
            history = history_for(state)
            desired = args.id or (state.get('panorama') if args.command in ('fit','reload','static') else None)
            if args.command=='previous':
                available = {row['id'] for row in eligible}
                history = history[:-1]
                while history and (history[-1] not in available or history[-1]==state.get('panorama')): history.pop()
                if not history: raise ValueError('No previous suitable panorama in this group')
                desired = history[-1]
            # Suitability only governs picking new images; the shown one is cropped to any layout (e.g. one monitor left).
            keep = args.command in ('fit','reload','static') and not args.id
            row = next((r for r in rows if r['id']==desired and (keep or framing(r,layout,settings)['valid'])),None)
            if args.id and row is None: raise ValueError('Requested panorama is unsuitable or unknown')
            seen = state.get('seen',[])
            if row is None:
                if not eligible: raise ValueError(unsuitable)
                available = [r for r in eligible if r['id'] not in seen and r['id']!=state.get('panorama')]
                if not available:
                    seen=[]
                    available=[r for r in eligible if r['id']!=state.get('panorama')] or eligible
                row = random.choice(available)
            if args.command=='render':
                if args.destination is None: raise ValueError('render requires --destination')
                frame=render(row,layout,settings,args.destination)
                print(json.dumps(dict(panorama=row['id'],framing=frame,destination=str(args.destination)),indent=2)); return
            generation=generation_path(row,layout,settings)
            if not ready(generation,layout):
                prepare([row],layout,settings)
            changed = apply(layout,generation)
            if row['id'] not in seen: seen.append(row['id'])
            if not history or history[-1]!=row['id']: history.append(row['id'])
            atomic(STATE/'state.json',json.dumps(dict(panorama=row['id'],title=row['title'],updated=time.time(),
                eligible=len(eligible),collection=len(rows),seen=seen,history=history[-50:],layout=layout,generation=str(generation)),indent=2))
            if args.command=='configure': save_settings(settings)
            verb = 'Applied' if changed else 'Already showing'
            print(f"{verb} {row['id']} on {', '.join(o['name'] for o in layout['outputs'])}; {len(eligible)} suitable panoramas",flush=True)
            if args.command=='static':
                systemctl('enable',f'{APP}-reload.service')
                systemctl('enable','--now',f'{APP}-layout.path')
                print('Static mode: this panorama is re-fitted after login and on monitor changes')
            if args.command=='reload':
                remaining=[r for r in rows if r['id']!=row['id'] and framing(r,layout,settings)['valid'] and (not args.group or args.group=='all' or r.get('group','default')==args.group)]
                prepare(remaining,layout,settings,fast=args.fast)
                prune(rows,layout,settings)
                print('Current panorama applied first; remaining cache ready')
            if args.command!='fit': break
            time.sleep(2)
            if geometry(settings)==layout: break


def cli():
    try: main()
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        sys.exit(f'{APP}: {error}')


if __name__=='__main__':
    cli()
