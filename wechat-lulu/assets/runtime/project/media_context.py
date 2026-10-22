"""按确切消息读取本机媒体，缩图和抽帧；不操作微信界面或上传原视频。"""
import hashlib,json,time,re
from pathlib import Path
from PIL import Image,ImageOps

MEDIA_TYPES={'图片':'image','image':'image',3:'image','3':'image','视频':'video','video':'video',43:'video','43':'video'}
MAX_IMAGES=4

def media_descriptor(message):
    kind=MEDIA_TYPES.get(message.get('type'))
    if not kind:return None
    return {'kind':kind,'server_id':int(message['server_id']) if message.get('server_id') is not None else None,**{k:int(message.get(k) or 0) for k in ('local_id','sort_seq','create_time')}}

def recent_media_reference(rows,incoming,now=None):
    now=time.time() if now is None else now
    explicit=bool(re.search(r'图|照片|视频|画面|这个|这是|什么颜色|里面|上面|是谁|字幕|写的|怎么看|看一下|好看吗|认得吗|牌子|型号|哪家的',incoming))
    candidates=[(i,media_descriptor(row)) for i,row in enumerate(rows) if str(row.get('sender_id'))!='2' and media_descriptor(row)]
    if not candidates:return []
    index,message=candidates[-1];age=now-message['create_time']
    answered=any(str(row.get('sender_id'))=='2' for row in rows[index+1:])
    # 原媒体可能在修复前发来，不能按原发送时间拒绝刚刚回复后的图片追问。
    # 仍只取最近8条中的一件媒体，明确追问保留7天；普通跟随消息仅120秒。
    if 0<=age<=604800 and (explicit or (not answered and age<=120)):return [message]
    return []

class ExactMessageDB:
    def __init__(self,db,user,row):self.db=db;self.user=user;self.row=row
    def __getattr__(self,name):return getattr(self.db,name)
    def get_message_row(self,user,local_id,local_type=None):
        if user!=self.user or local_id!=self.row['local_id'] or local_type!=self.row['local_type']:raise RuntimeError('媒体读取目标不匹配')
        return self.row

def bounded_image(source,target,max_side=1024):
    with Image.open(source) as picture:
        if picture.width*picture.height>40_000_000:raise ValueError('图片过大')
        picture=ImageOps.exif_transpose(picture).convert('RGB')
        picture.thumbnail((max_side,max_side))
        picture.save(target,'JPEG',quality=82)
    return str(Path(target).resolve())

def sample_video(source,directory,count=4):
    import cv2
    capture=cv2.VideoCapture(str(source));frames=[]
    try:
        total=int(capture.get(cv2.CAP_PROP_FRAME_COUNT));fps=capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or total<1 or fps<=0:raise ValueError('视频不可解码')
        duration=total/fps
        # 全片均匀抽样，给模型时间标签；不把关键帧当成完整视频或音频。
        for index in range(min(count,total)):
            frame=min(total-1,round((total-1)*(index+.5)/min(count,total)))
            capture.set(cv2.CAP_PROP_POS_FRAMES,frame);ok,picture=capture.read()
            if not ok:continue
            image=Image.fromarray(cv2.cvtColor(picture,cv2.COLOR_BGR2RGB))
            image.thumbnail((768,768));path=Path(directory)/('frame-'+str(index)+'.jpg');image.save(path,'JPEG',quality=80)
            frames.append({'path':str(path.resolve()),'second':round(frame/fps,2)})
        if not frames:raise ValueError('未获得视频画面')
        return frames,round(duration,2)
    finally:capture.release()

class MediaContext:
    def __init__(self,db,root,allowed):self.db=db;self.root=Path(root);self.allowed=frozenset(allowed)
    def _row(self,user,message):
        kind=3 if message['kind']=='image' else 43
        candidates=[r for r in self.db.get_message_rows_for_media(user,message['local_id']) if r.get('local_type')==kind and int(r.get('sort_seq') or 0)==message['sort_seq'] and (message.get('server_id') is None or int(r.get('server_id') or 0)==message['server_id']) and int(r.get('create_time') or 0)==message['create_time']]
        if len(candidates)!=1:raise RuntimeError('媒体消息不能唯一对应，停止识别')
        return candidates[0]
    def prepare(self,user,messages):
        if user not in self.allowed:raise RuntimeError('媒体联系人未授权')
        from wechatauto import MediaDownloader
        class ImmediateDownloader(MediaDownloader):
            def _scan_aes_key(self,monitor=False,monitor_timeout=0):
                # 原库缺密钥会等待人工打开图片120秒；监听时仅做一次只读扫描。
                return super()._scan_aes_key(monitor=False)
            def decrypt_image(self,dat_path,*args,**kwargs):
                # 以当前图片校验，不使用目录里年代不同的第一张旧图作为探针。
                self._key_probe=None;self._probe_ct(dat_path)
                return super().decrypt_image(dat_path,*args,**kwargs)
        images=[];descriptions=[]
        for message in messages:
            identity=hashlib.sha256((user+'|'+json.dumps(message,sort_keys=True)).encode()).hexdigest()[:24]
            directory=self.root/identity;directory.mkdir(parents=True,exist_ok=True)
            description={'kind':message['kind'],'seq':message['sort_seq']}
            if len(images)>=MAX_IMAGES:
                descriptions.append({**description,'status':'omitted-limit'});continue
            try:
                row=self._row(user,message);downloader=ImmediateDownloader(ExactMessageDB(self.db,user,row),save_dir=str(directory))
                path=None
                for attempt in range(3):
                    path=downloader.download_image(user,message['local_id'],tier='best') if message['kind']=='image' else self._video_path(row)
                    if path:break
                    if attempt<2:time.sleep(1)
                if not path:
                    descriptions.append({**description,'status':'not-cached'});continue
                if message['kind']=='image':
                    target=bounded_image(path,directory/'vision.jpg');images.append(target)
                    description.update(status='ready',image_numbers=[len(images)],quality='local-cached-copy')
                else:
                    frames,duration=sample_video(path,directory,count=MAX_IMAGES-len(images))
                    labels=[]
                    for frame in frames:
                        images.append(frame['path']);labels.append({'image_number':len(images),'second':frame['second']})
                    description.update(status='ready',duration_seconds=duration,frames=labels,audio='not-transcribed',coverage='sampled-frames-only')
                descriptions.append(description)
            except Exception as error:
                descriptions.append({**description,'status':'unavailable','error_type':type(error).__name__})
        return {'images':images,'descriptions':descriptions}
    def _video_path(self,row):
        # 视频直接抽本机缓存，避免下载器整段复制到内存；不用媒体原图 UIA 接口。
        packed=row.get('packed_info')
        match=re.search(rb'([0-9a-fA-F]{32})',packed) if isinstance(packed,bytes) else None
        if not match:return None
        base=Path(self.db.account_dir)/'msg'/'video'
        paths=list(base.glob('*/'+match[1].decode().lower()+'.mp4'))
        if len(paths)!=1:return None
        if paths[0].stat().st_size>500*1024*1024:raise ValueError('视频超过本机抽帧大小上限')
        return str(paths[0].resolve())
