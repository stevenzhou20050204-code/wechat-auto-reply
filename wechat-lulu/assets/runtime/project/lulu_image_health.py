"""生图链路就绪检查；只查规则、文件与原生功能元数据，绝不开始生成轮次。"""
import argparse,datetime,json,sys,tempfile
from pathlib import Path
from lulu_bridge import LuluBridge,image_tools_needed
from passive_sender import PassiveSender
from codex_bridge import CodexBridge

CREATION_CASES=('画一只白鹭','给我一张白鹭的图','做个头像','那你给我一张虚拟自拍','来张自拍','make a selfie')
READING_CASES=('你好','现在几点','这张图片里是什么？','这段视频里是谁？')

def resolve_cli(engine_root):
    sys.path.insert(0,str(engine_root))
    from reply_engine import resolve_native_cli
    return resolve_native_cli()

def probe_native(engine_root,state_root,bridge_factory=None):
    """initialize/account/read由桥接初始化执行，随后仅允许读取功能列表。"""
    cli=Path(resolve_cli(engine_root))
    if not cli.is_file():raise RuntimeError('当前原生Codex程序不存在')
    factory=bridge_factory or CodexBridge
    bridge=None;feature=None
    try:
        bridge=factory(state_path=Path(state_root)/'image-capability'/'bridge.json',cwd=engine_root,
            model='gpt-6.1-sol',command=[str(cli),'-c','features.image_generation=true','app-server'],
            request_timeout=10,turn_timeout=1)
        cursor=None
        for _ in range(4):
            params={'limit':100}
            if cursor:params['cursor']=cursor
            result=bridge.request('experimentalFeature/list',params)
            feature=next((row for row in result.get('data',[]) if row.get('name')=='image_generation'),None)
            if feature or not result.get('nextCursor'):break
            cursor=result['nextCursor']
        if not feature or feature.get('enabled') is not True:raise RuntimeError('原生运行程序未确认image_generation功能开关')
        return {'supported':True,'enabled':True,'cli':str(cli),'accountType':bridge.account_type,
            'method':'experimentalFeature/list','generationCalls':0}
    finally:
        if bridge is not None:bridge.close()

def check_image_health(root,engine_root,native=True,bridge_factory=None):
    root=Path(root);engine_root=Path(engine_root);state=root/'.wechat_codex'
    result={'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'ok':False,'status':'failed','modelInvoked':False,'generationTested':False,'generatedImageCount':0,'issues':[],'checks':{}}
    try:
        config=json.loads((root/'wechat_autoreply.json').read_text(encoding='utf-8-sig'))
        checks=result['checks']
        checks['assistantConfigured']=config.get('version')==2 and config.get('enabled') is True and config.get('assistant_name')=='鹭鹭'
        checks['creationPhrases']=all(image_tools_needed({'incoming':text}) for text in CREATION_CASES)
        checks['followupCreationIntent']=all(image_tools_needed({'incoming':text,'recent':[{'role':'contact','text':'给我一张虚拟自拍'},{'role':'owner_or_assistant','text':'暂时用不了生图'},{'role':'contact','text':'快点'},{'role':'owner_or_assistant','text':'稍等'},{'role':'contact','text':'发呀'}]}) for text in ('赶紧发','现在还没有？','好了没'))
        checks['readingDoesNotGenerate']=all(not image_tools_needed({'incoming':text}) for text in READING_CASES)
        bridge=LuluBridge.__new__(LuluBridge);bridge.cwd=engine_root/'lulu-runtime';bridge.model='gpt-6.1-sol';bridge.image_tools=True
        params=bridge._thread_params();features=params['config']
        checks['imageToolConfigured']=features.get('features.image_generation') is True
        checks['restrictedToolsRemainDisabled']=all(features.get('features.'+name) is False for name in ('shell_tool','unified_exec','apps','plugins','computer_use'))
        # 走真实generate的载入分支，但用固定JSON替代网络回复，零模型调用。
        probe=LuluBridge.__new__(LuluBridge);probe.output_root=engine_root
        probe.reply=lambda *a,**k:json.dumps({'decision':'reply','text':'本地规则检查','important':False,'summary':'','sources':[]})
        checks['followupToolAvailable']=all(probe.generate('isolated-health',{'incoming':text},'本地检查').get('image_tool_enabled') is True for text in ('赶紧发','现在还没有？'))
        checks['imageSenderAvailable']=all(callable(getattr(PassiveSender,name,None)) for name in ('prepare_image','finish_image','send_image'))
        output=state/'lulu'/'worker-0'/'generated';output.mkdir(parents=True,exist_ok=True)
        # 只检查普通临时文件可写，不创建图片、不覆盖旧图片。
        with tempfile.TemporaryFile(dir=output) as stream:stream.write(b'image-health-no-generation');stream.flush()
        checks['outputWritable']=True
        result['issues'].extend(name+'检查未通过' for name,value in checks.items() if not value)
        metrics=state/'lulu'/'generation-metrics.jsonl'
        if metrics.exists():
            # 只看技术指标尾部，不读取联系人消息正文，不重放真实生成请求。
            with metrics.open('rb') as stream:
                offset=max(0,metrics.stat().st_size-65536);stream.seek(offset)
                if offset:stream.readline()
                rows=[json.loads(line) for line in stream.read().decode('utf-8').splitlines() if line.strip()]
            attempts=[row for row in rows if row.get('image_requested') and row.get('image_tool_enabled')]
            if attempts:
                last=attempts[-1]
                result['lastRealRequest']={key:last.get(key) for key in ('at','generated_image','image_attempts','image_errors')}
                age=datetime.datetime.now(datetime.timezone.utc).timestamp()-float(last.get('at',0))
                if last.get('image_errors') and not last.get('generated_image') and 0<=age<=4500:
                    result['issues'].append('最近真实生图请求失败：'+'；'.join(map(str,last['image_errors']))[:200])
        if native:
            result['native']=probe_native(engine_root,state,bridge_factory)
        else:result['native']={'status':'not_probed','generationCalls':0}
        result['ok']=not result['issues']
        result['status']='ready_without_generation_test' if result['ok'] else 'failed'
    except Exception as error:result['issues'].append(str(error)[:300])
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--engine-root',required=True,type=Path)
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parent;output=args.output or root/'.wechat_codex'/'image-generation-health.json'
    result=check_image_health(root,args.engine_root);output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(output)
    print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 2

if __name__=='__main__':raise SystemExit(main())
