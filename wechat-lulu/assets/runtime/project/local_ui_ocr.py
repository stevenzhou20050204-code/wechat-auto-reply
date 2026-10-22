"""本机中文OCR补足WinRT漏字；图片不上传，模型在本机缓存。"""
import threading
_engine=None
_lock=threading.RLock()

def recognize(picture,minimum_score=.90):
    global _engine
    from rapidocr import RapidOCR
    with _lock:
        if _engine is None:
            # 默认min模式把窄标题裁图的短边放大到736，造成数千像素宽的巨图和发送超时。
            _engine=RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,'Det.limit_type':'max','Det.limit_side_len':1280,'Global.log_level':'warning'})
        result=_engine(picture)
    if result.boxes is None:return []
    rows=[]
    for points,text,score in zip(result.boxes,result.txts,result.scores):
        if score<minimum_score:continue
        x=int(points[:,0].min());y=int(points[:,1].min())
        rows.append((text,x,y,int(points[:,0].max())-x,int(points[:,1].max())-y))
    return rows

def window_rows(gui,box,minimum_score=.90):
    rows=recognize(gui._grab_screen(gui._rel_to_screen(box)),minimum_score=minimum_score)
    return [(text,x+box[0],y+box[1],w,h) for text,x,y,w,h in rows]
