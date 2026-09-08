"""Documentation-only 3D renderer. Existing acquisition/analysis code is untouched.

Run from the repository root: python tools/render_portfolio.py
Requires numpy, Pillow, PyYAML. Uses a fixed calibration tag, never directory mtime.
The four pure NumPy triangulation functions are compiled from their existing AST
so rendering does not require the HDF5/Qt/hardware stack. No algorithm is copied
or replaced. This is a geometry illustration, not an optical/refraction simulator.
"""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import math
from types import SimpleNamespace

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'media/portfolio'
TAG = 'charucofin0825_20260825_153755'
CAL = ROOT / f'multiview3d/calibrations/{TAG}/calibration_{TAG}.json'
BG = (10, 19, 32)
FG = (231, 240, 248)
MUTED = (150, 170, 191)
CYAN = (77, 216, 218)
GOLD = (249, 185, 98)
COLORS = [CYAN, (123, 164, 255), GOLD, (239, 130, 161)]
W, H = 1200, 760
FPS, N = 16, 96
FONT = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
if not FONT.exists():
    FONT = Path('C:/Windows/Fonts/arial.ttf')


def font(n):
    return ImageFont.truetype(str(FONT), n) if FONT.exists() else ImageFont.load_default()


def text(d, xy, s, size=20, color=FG):
    d.text(xy, s, font=font(size), fill=color)


def base(title, sub, footer='SYNTHETIC LARVAE / MOTION  |  SAVED CAMERA CALIBRATION  |  ILLUSTRATIVE OPTICS'):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    text(d, (36, 23), 'RIG STUDIO   /   COMPUTATIONAL BEHAVIOR', 16, CYAN)
    text(d, (36, 56), title, 33)
    text(d, (36, 105), sub, 18, MUTED)
    d.line((36, H-55, W-36, H-55), fill=(45, 63, 83), width=1)
    text(d, (36, H-37), footer, 13, MUTED)
    return im


def load_core():
    path = ROOT / 'multiview3d/triangulate3d.py'
    names = {'triangulate_dlt', 'reproject_px', '_solve', 'triangulate_views'}
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    nodes += [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'REPROJ_DROP_PX' for t in n.targets)]
    assert len(nodes) == 5
    scope = {'np': np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return SimpleNamespace(**{k: scope[k] for k in names})


def load():
    data = json.loads(CAL.read_text())
    cams = {}
    for key, c in data['cameras'].items():
        c = {k: np.asarray(c[k], float) for k in ('K', 'R', 't')}
        c['P'] = c['K'] @ np.column_stack((c['R'], c['t']))
        c['C'] = -c['R'].T @ c['t']
        cams[int(key)] = c
    return cams


def mesh_grid(grid, color, shade=True):
    faces = np.stack((grid[:-1, :-1], grid[1:, :-1], grid[1:, 1:], grid[:-1, 1:]), axis=2).reshape(-1, 4, 3)
    colors = np.tile(color, (len(faces), 1)).astype(float) if np.ndim(color) == 1 else np.asarray(color).reshape(-1, 3)
    if shade:
        normals = np.cross(faces[:, 1]-faces[:, 0], faces[:, 3]-faces[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
        light = np.array([-.25, -.5, -.8]); light /= np.linalg.norm(light)
        colors *= (.47 + .53*np.abs(normals@light))[:, None]
    return [(p, tuple(np.clip(c, 0, 255).astype(int))) for p, c in zip(faces, colors)]


def box(center, size, color, rot=None):
    p = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]], float)*np.array(size)/2
    if rot is not None:
        p = p @ rot.T
    p += center
    ids = [[0,1,2,3],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    return [(p[j], tuple(np.array(color)*(.58+.08*i))) for i, j in enumerate(ids)]


def tube(a, b, radius, color, n=18):
    a, b = np.asarray(a), np.asarray(b)
    axis = b-a; axis /= np.linalg.norm(axis)
    v = np.cross(axis, [0., 1., 0.])
    if np.linalg.norm(v) < .01:
        v = np.cross(axis, [1., 0., 0.])
    v /= np.linalg.norm(v); u = np.cross(axis, v)
    th = np.linspace(0, 2*np.pi, n+1)
    ring = radius*(np.cos(th)[:,None]*u+np.sin(th)[:,None]*v)
    faces = mesh_grid(np.array([a+ring, b+ring]), color)
    faces.append((b+ring, tuple(color)))
    return faces


def larva(t, lane=0):
    # An illustrative 5 mm larva. No species-specific size or performance claim.
    phase = 2*np.pi*t/6
    head = np.array([93+56*np.sin(phase), 10+lane+2*np.cos(phase), -11-5*np.sin(phase+.7)])
    heading = np.arctan2(-2*np.sin(phase), 56*np.cos(phase))
    fwd = np.array([np.cos(heading), np.sin(heading), 0.])
    side = np.array([-fwd[1], fwd[0], 0.])
    s = np.linspace(0, 1, 25)
    line = head - 5*s[:,None]*fwd + .42*s[:,None]**1.6*np.sin(2*np.pi*(5*t-s))[:,None]*side
    theta = np.linspace(0, 2*np.pi, 17)
    width = np.interp(s, [0,.08,.2,.4,.65,.9,1], [.10,.40,.38,.23,.12,.05,.012])
    grid = line[:,None,:] + width[:,None,None]*(np.cos(theta)[None,:,None]*side + .85*np.sin(theta)[None,:,None]*np.array([0,0,1]))
    faces = mesh_grid(grid, [170,197,205])
    for sg in [-1,1]:
        eye = head-.45*fwd+sg*.28*side+np.array([0,0,-.16])
        a, b = np.meshgrid(np.linspace(0,np.pi,9),np.linspace(0,2*np.pi,13), indexing='ij')
        sphere = eye+.17*np.stack((np.sin(a)*np.cos(b),np.sin(a)*np.sin(b),np.cos(a)), axis=-1)
        faces += mesh_grid(sphere, [17,25,32])
    # Thin translucent-looking finfold, represented with subdued opaque material.
    fin = line[8:].copy(); fin[:,2] -= .20*np.sin(np.linspace(0,np.pi,len(fin)))
    faces += mesh_grid(np.stack((line[8:],fin)), [92,125,137])
    nodes = np.array([line[0], line[8], line[16], line[24]])
    return faces, nodes, line


def project_view(points, yaw=-.24, pitch=.43, scale=3.5, center=(600,400), target=(90,10,0)):
    p = np.asarray(points)-np.asarray(target)
    x, y, z = p[...,0], p[...,1], -p[...,2]
    xx = x*np.cos(yaw)-y*np.sin(yaw)
    yy = x*np.sin(yaw)+y*np.cos(yaw)
    zz = z*np.cos(pitch)+yy*np.sin(pitch)
    depth = yy*np.cos(pitch)-z*np.sin(pitch)
    factor = 1/(1+depth/700)
    return np.stack((center[0]+scale*xx*factor, center[1]-scale*zz*factor),axis=-1), depth


def draw_mesh(im, faces, projection):
    d = ImageDraw.Draw(im)
    items = []
    for face, col in faces:
        q, depth = projection(face)
        if not np.all(np.isfinite(q)) or np.max(np.abs(q)) > 20000:
            continue
        items.append((float(np.mean(depth)), q, col))
    for _, q, col in sorted(items, key=lambda x:x[0], reverse=True):
        d.polygon([tuple(p) for p in q], fill=tuple(int(v) for v in col))


def curve(d, points, project, color, width=2):
    xy, _ = project(np.array(points))
    if np.all(np.isfinite(xy)):
        d.line([tuple(p) for p in xy], fill=color, width=width)


def trough(t, grating, hardware, cams):
    theta, x = np.meshgrid(np.linspace(0,np.pi,33), np.linspace(-12,193,165))
    grid = np.stack((x, 10+25*np.cos(theta), -22.5+25*np.sin(theta)), axis=-1)
    # Same luminance function as stimulus; texture-to-world orientation is illustrative.
    lum = grating.profile((x[:-1,:-1]+x[1:,:-1])/2*7.858, 157.5, -78.6*t)
    col = np.stack((40+lum*149, 60+lum*140, 77+lum*130), axis=-1)
    faces = mesh_grid(grid,col)
    for y in [-15.,35.]:
        faces += tube([-12.,y,-22.5],[193.,y,-22.5],.65,[175,200,212])
    if hardware:
        for cid, c in cams.items():
            C, R = c['C'], c['R']
            axis = R.T@np.array([0.,0.,1.])
            # Lens centre is at calibrated C; body dimensions are illustrative.
            faces += box(C-axis*12,[13,13,19],[69,78,90],R.T)
            faces += tube(C-axis*5,C+axis*5,4.8,[51,62,75])
            faces += tube(C+axis*5,C+axis*5.6,4.1,[64,29,48])
            faces += tube(C-axis*5.4,C-axis*4.7,5,[164,176,188])
        faces += box([90,10,74],[51,40,18],[59,70,82])
        faces += tube([90.,10.,65.],[90.,10.,62.],8,[134,180,203])
    return faces


def save_gif(frames, name):
    # Shared adaptive palette avoids frame-to-frame palette flicker.
    palette = frames[0].quantize(colors=256)
    frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in frames]
    frames[0].save(OUT/name, save_all=True, append_images=frames[1:], duration=[60,60,60,70]*(len(frames)//4), loop=0, disposal=2, optimize=False)


def observations(cams, core):
    rng = np.random.default_rng(23)
    truth = np.array([larva(i/FPS)[1] for i in range(N)])
    det = np.full((N,6,4,2),np.nan); bad=np.zeros((N,6,4),bool)
    reconstructed = np.full_like(truth,np.nan); used=np.zeros((N,4),int)
    residual=np.full((N,4),np.nan); available=np.zeros((N,4),int)
    crops=[(1472,1450),(2048,1450),(2048,1450),(1472,1450),(2048,1408),(2048,1408)]
    for i in range(N):
        for cid,c in cams.items():
            for n,X in enumerate(truth[i]):
                uv=core.reproject_px(c['P'],X)
                if (c['R']@X+c['t'])[2]<=0 or not (0<=uv[0]<crops[cid][0] and 0<=uv[1]<crops[cid][1]) or rng.random()<.04:
                    continue
                uv=uv+rng.normal(0,1.5,2)
                if rng.random()<.03:
                    uv+=rng.uniform(25,70,2)*rng.choice([-1,1],2); bad[i,cid,n]=True
                det[i,cid,n]=uv
        for n in range(4):
            views=[(cid,det[i,cid,n]) for cid in cams if np.isfinite(det[i,cid,n]).all()]
            available[i,n]=len(views)
            if len(views)>=2:
                reconstructed[i,n],residual[i,n],used[i,n]=core.triangulate_views(views,cams)
    err=np.linalg.norm(reconstructed-truth,axis=-1)
    stats={'calibration_tag':TAG,'seed':23,'frames':N,'illustration_fps':FPS,'illustrative_larva_length_mm':5,
           'median_3d_error_mm':float(np.nanmedian(err)),'p95_3d_error_mm':float(np.nanpercentile(err,95)),
           'coverage':float(np.isfinite(err).mean()),'views_rejected':int(np.maximum(available-used,0)[used>=2].sum()),
           'noise_sigma_px':1.5,'miss_probability':.04,'outlier_probability':.03,
           'scope':'Synthetic single-larva illustration. Not live performance, biological evidence, or a 120 Hz acquisition benchmark.'}
    (OUT/'simulation_stats.json').write_text(json.dumps(stats,indent=2)+'\n')
    return truth,det,bad,reconstructed,used,residual,err,crops


def render_hardware(cams, gr):
    frames=[]
    for i in range(64):
        t=i/FPS
        im=base('Six views. One shared 3D world.', '4 overhead cameras + 2 end views  /  FLIR Grasshopper3  /  RG830 filters')
        project=lambda p:project_view(p,yaw=-.23+.09*np.sin(2*np.pi*i/64),pitch=.40,scale=3.1,center=(598,412),target=(90,10,10))
        faces=trough(t,gr,True,cams)
        draw_mesh(im,faces,project)
        d=ImageDraw.Draw(im)
        for cid,c in cams.items():
            q,_=project(c['C']-c['R'].T@np.array([0.,0.,26.]))
            text(d,(q[0]-20,q[1]-15),f'C{cid}',18,CYAN if cid<4 else GOLD)
        # A dashed cone explicitly depicts a schematic light path, not a ray trace.
        for end in [[-12,10,2.5],[193,10,2.5]]:
            a=np.array([90,10,60]); b=np.array(end)
            for k in range(0,20,2):
                curve(d,[a+(b-a)*k/20,a+(b-a)*(k+1)/20],project,(75,128,150),1)
        text(d,(38,565),'PROJECTOR BELOW',20,CYAN)
        text(d,(38,597),'Rear projection onto the coated curved surface',18,MUTED)
        text(d,(740,565),'DEPTH FROM COMPLEMENTARY VIEWS',18,GOLD)
        text(d,(740,597),'End cameras look through the flat windows',17,MUTED)
        text(d,(38,653),'Camera centres / axes from calibration; housings and projector placement are schematic.',16,MUTED)
        if i==0: im.save(OUT/'rig_geometry.png')
        frames.append(im)
    save_gif(frames,'rig_overview.gif')


def render_stimulus(gr,cams):
    frames=[]
    for i in range(64):
        t=i/FPS
        im=base('The visual environment is the racecourse.', 'Moving grating on a curved screen  /  20.0 mm period  /  10.0 mm/s axial drift')
        project=lambda p:project_view(p,yaw=-.33,pitch=.70,scale=4.7,center=(600,390),target=(90,10,-8))
        draw_mesh(im,trough(t,gr,False,cams),project)
        d=ImageDraw.Draw(im)
        # Larvae drawn in a cutaway overlay so the near wall does not hide them.
        for lane,color,label in [(-5,CYAN,'A'),(5,GOLD,'B')]:
            faces,nodes,_=larva(t+lane*.025,lane)
            draw_mesh(im,faces,project)
            q,_=project(nodes[0]);d.ellipse((q[0]-7,q[1]-7,q[0]+7,q[1]+7),outline=color,width=2)
            text(d,(q[0]+10,q[1]-20),label,17,color)
        text(d,(40,545),'SPEED',18,CYAN);text(d,(40,578),'How quickly does a larva advance?',17,MUTED)
        text(d,(433,545),'PERSISTENCE',18,CYAN);text(d,(433,578),'How long does it keep responding?',17,MUTED)
        text(d,(842,545),'DEPTH CHOICE',18,CYAN);text(d,(842,578),'Where does it choose to swim?',17,MUTED)
        text(d,(40,644),'Two illustrative larvae share the stimulus. Motion is invented; this is not a measured species comparison.',16,MUTED)
        frames.append(im)
    save_gif(frames,'stimulus_3d.gif')


def render_views(cams,data):
    truth,det,bad,rec,used,residual,err,crops=data
    frames=[]
    for i in range(N):
        im=base('From six images to body keypoints.', 'Synthetic monochrome projections  /  calibrated image crops  /  four anatomical nodes')
        d=ImageDraw.Draw(im)
        for order,cid in enumerate([3,2,1,0,5,4]):
            x=38+(order%3)*385;y=156+(order//3)*265
            cw,ch=crops[cid]; iw,ih=363,212
            tile=Image.new('RGB',(iw,ih),(28,33,39))
            c=cams[cid]
            def proj(p):
                q=np.asarray(p)@c['R'].T+c['t']; uv=q@c['K'].T
                uv=uv[:,:2]/uv[:,2:3]
                return uv*np.array([iw/cw,ih/ch]),q[:,2]
            # Crop is displayed with aspect preserved by a common scale.
            scale=min(iw/cw,ih/ch); ox=(iw-cw*scale)/2; oy=(ih-ch*scale)/2
            def proj(p):
                q=np.asarray(p)@c['R'].T+c['t']; uv=q@c['K'].T
                return uv[:,:2]/uv[:,2:3]*scale+[ox,oy],q[:,2]
            td=ImageDraw.Draw(tile)
            for yy in [0.,20.]:
                pts=np.array([[xx,yy,0.] for xx in np.linspace(0,180,50)])
                q,dep=proj(pts)
                good=dep>0
                if good.all(): td.line([tuple(p) for p in q],fill=(56,64,72),width=1)
            draw_mesh(tile,larva(i/FPS)[0],proj)
            td=ImageDraw.Draw(tile)
            for n in range(4):
                uv=det[i,cid,n]
                if not np.isfinite(uv).all():continue
                q=uv*scale+[ox,oy]
                if bad[i,cid,n]:
                    td.line((q[0]-4,q[1]-4,q[0]+4,q[1]+4),fill=(249,100,110),width=2)
                    td.line((q[0]-4,q[1]+4,q[0]+4,q[1]-4),fill=(249,100,110),width=2)
                else:td.ellipse((q[0]-3,q[1]-3,q[0]+3,q[1]+3),fill=COLORS[n])
            im.paste(tile,(x,y+29))
            text(d,(x,y),f'C{cid}  '+('OVERHEAD' if cid<4 else 'END / DEPTH')+f'   {cw} × {ch}',16,CYAN if cid<4 else GOLD)
        text(d,(40,674),'Head  /  body  /  tail base  /  tail end     •     red × = injected outlier     •     missing detections remain missing',14,MUTED)
        frames.append(im)
    save_gif(frames,'six_views_3d.gif')


def render_reconstruction(data):
    truth,det,bad,rec,used,residual,err,crops=data
    frames=[]
    for i in range(N):
        im=base('Reconstruct pose. Keep the uncertainty.', 'The existing DLT + pairwise-consensus functions reconstruct the synthetic observations')
        d=ImageDraw.Draw(im)
        # Close-up follows the larva; no claim that these are observed microscope images.
        target=np.mean(truth[i],axis=0)
        project=lambda p:project_view(p,yaw=-.22,pitch=.57,scale=85,center=(340,185),target=target)
        panel=Image.new('RGB',(740,340),BG)
        draw_mesh(panel,larva(i/FPS)[0],project)
        d=ImageDraw.Draw(panel)
        q,_=project(rec[i])
        for n in range(3):
            if np.isfinite(q[n:n+2]).all(): d.line([tuple(q[n]),tuple(q[n+1])],fill=CYAN,width=3)
        for n,p in enumerate(q):
            if np.isfinite(p).all():d.ellipse((p[0]-6,p[1]-6,p[0]+6,p[1]+6),fill=COLORS[n],outline=FG,width=1)
        im.paste(panel,(30,165))
        d=ImageDraw.Draw(im)
        text(d,(40,164),'3D POSE / MAGNIFIED LARVA',18,CYAN)
        text(d,(800,166),'RECONSTRUCTION QUALITY',18,CYAN)
        for n,name in enumerate(['head','body','tail base','tail end']):
            y=207+n*72
            text(d,(800,y),name.upper(),15,COLORS[n])
            val=f'{err[i,n]:.2f} mm' if np.isfinite(err[i,n]) else 'missing'
            text(d,(800,y+23),f'{val}   /   {used[i,n]} views',22)
        text(d,(40,518),'Synthetic 3D error over this six-second example',18,MUTED)
        x0,y0,x1,y1=45,660,733,558
        ceiling=max(1.,float(np.nanmax(err)))
        d.line((x0,y1,x0,y0,x1,y0),fill=(80,99,119),width=1)
        for n in range(4):
            pts=[(x0+k/(N-1)*(x1-x0),y0-min(float(err[k,n]),ceiling)/ceiling*(y0-y1)) for k in range(i+1) if np.isfinite(err[k,n])]
            if len(pts)>1:d.line(pts,fill=COLORS[n],width=2)
        text(d,(50,560),f'{ceiling:.1f} mm',13,MUTED)
        text(d,(800,550),'Geometry check only',20,GOLD)
        text(d,(800,584),'No trained model evaluated.',16,MUTED)
        text(d,(800,611),'No live accuracy implied.',16,MUTED)
        text(d,(800,650),f't = {i/FPS:4.2f} s',19,CYAN)
        frames.append(im)
    save_gif(frames,'pose_reconstruction.gif')


def render_timing():
    frames=[]
    for i in range(48):
        im=base('Synchronize acquisition. Log the stimulus.', '120 Hz hardware camera trigger  /  240 Hz configured projector refresh', 'SCHEMATIC TIMING  |  5 ms EXPOSURE  |  PROJECTOR PHASE IS ILLUSTRATIVE, NOT MEASURED GENLOCK')
        d=ImageDraw.Draw(im); left,right=230,1150
        for j,label in enumerate(['TRIGGER']+[f'CAMERA {j}' for j in range(6)]+['PROJECTOR']):
            y=178+j*53
            text(d,(40,y-7),label,17,CYAN if j<7 else GOLD)
            d.line((left,y+18,right,y+18),fill=(65,82,101),width=1)
            period=8.333 if j<7 else 4.167
            width=1.0 if j==0 else 5.0 if j<7 else .4
            offset=0 if j<7 else 1.0
            for t in np.arange(offset,33.334,period):
                x=left+t/33.334*(right-left);end=min(right,x+width/33.334*(right-left))
                d.rectangle((x,y,end,y+18),fill=CYAN if j<7 else GOLD)
        cursor=left+i/47*(right-left)
        d.line((cursor,163,cursor,605),fill=FG,width=2)
        for j in range(5):text(d,(left+j*(right-left)/4-12,613),f'{j*8.333:.1f}',14,MUTED)
        text(d,(40,659),'Hardware frame IDs align camera streams. Per-flip host timestamps relate behavior to stimulus timing.',17,MUTED)
        frames.append(im)
    save_gif(frames,'acquisition_timing.gif')


def main():
    OUT.mkdir(exist_ok=True,parents=True)
    spec=importlib.util.spec_from_file_location('portfolio_grating',ROOT/'rig_studio/stimulus/grating.py')
    gr=importlib.util.module_from_spec(spec);spec.loader.exec_module(gr)
    conf=yaml.safe_load((ROOT/'configs/rig.yaml').read_text())
    assert conf['stimulus']['grating']['period_px']==157.5
    assert conf['stimulus']['grating']['speed_px_s']==78.6
    cams=load();core=load_core();data=observations(cams,core)
    # Exact projection/reconstruction self-check on every available camera.
    for X in [[90.,10.,-11.],[40.,7.,-6.],[140.,12.,-18.]]:
        X=np.array(X);views=[(c,core.reproject_px(v['P'],X)) for c,v in cams.items()]
        recovered,residual,used=core.triangulate_views(views,cams)
        assert np.linalg.norm(recovered-X)<1e-6 and residual<1e-6 and used==6
    for label,fn in [('hardware',lambda:render_hardware(cams,gr)),('stimulus',lambda:render_stimulus(gr,cams)),('views',lambda:render_views(cams,data)),('pose',lambda:render_reconstruction(data)),('timing',render_timing)]:
        print('Rendering',label,flush=True);fn()
    manifest={'calibration':str(CAL.relative_to(ROOT)),'calibration_sha256':hashlib.sha256(CAL.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
              'triangulation_sha256':hashlib.sha256((ROOT/'multiview3d/triangulate3d.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
              'hash_encoding':'UTF-8 text with LF-normalized line endings','seed':23,'camera_count':6,'top_cameras':[0,1,2,3],'end_cameras':[4,5],
              'geometry':'205 mm long, radius 25 mm; board z is down; camera centres are exact; body, projector and optical appearance are schematic.',
              'larvae':'5 mm illustrative larva; synthetic motion; A/B are not inferred species identities.',
              'optics':'No refraction, lens distortion, projector ray trace, or calibrated spectral-response model.',
              'stimulus':'Existing profile() used with axial 7.858 px/mm mapping; curved-surface texture mapping is illustrative.'}
    (OUT/'render_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Done',flush=True)


if __name__=='__main__':
    main()
