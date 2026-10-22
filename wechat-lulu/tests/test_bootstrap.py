"""隔离回归：不安装应用、不注册真实任务、不连接模型、不操作微信。"""
import ast,datetime,importlib.util,json,os,sys,time,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
SKILL=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SKILL/"scripts"))
import bootstrap as b
import service_host as s
WORK=Path(os.environ.get("LULU_TEST_WORK",str(SKILL.parent.parent/"bootstrap-test-artifacts")))
WORK.mkdir(parents=True,exist_ok=True)

def new_root():
    path=(WORK/uuid.uuid4().hex).absolute()
    assert path.parent.resolve()==WORK.resolve()
    return path

class BootstrapTests(unittest.TestCase):
    def fixture(self):
        root=b.claim(new_root());p=b.make_profile(root);b.stage_runtime(root,p)
        return root,p

    def test_reject_foreign_directory_and_links(self):
        root=new_root();root.mkdir();(root/"keep.txt").write_text("keep")
        with self.assertRaises(ValueError):b.claim(root)
        self.assertEqual((root/"keep.txt").read_text(),"keep")
        self.assertFalse((root/".lulu-bootstrap-owner.json").exists())
        for p in (Path.home(),SKILL,Path(SKILL.anchor)):
            with self.assertRaises(ValueError):b.validate_root(p)

    def test_foreign_listener_no_changes(self):
        root=new_root()
        with patch.object(b,"foreign_listener",return_value=True):
            result=b.install(root)
        self.assertEqual(result["status"],"existing_deployment")
        self.assertFalse(root.exists())

    def test_stage_preserves_private_state_and_pauses(self):
        root,p=self.fixture();state=root/"project/.wechat_codex";state.mkdir()
        records={"STOP":b"human-stop","UI-PAUSE":b"human-pause","reply-queue.json":b'{"keep":true}',"deliveries.sqlite3":b"fixture-ledger","fingerprint.json":b"fixture-watermark"}
        for name,data in records.items():(state/name).write_bytes(data)
        b.stage_runtime(root,p)
        for name,data in records.items():self.assertEqual((state/name).read_bytes(),data)
        self.assertFalse(b.load(root/"project/wechat_autoreply.json")["enabled"])
        self.assertEqual(b.load(root/"rdc/star-watch-policy.json")["intervalSeconds"],3600)

    def test_generated_runtime_python_and_powershell_parse(self):
        root,p=self.fixture()
        powershells=[]
        for rel in b.verified_sources():
            f=root/rel
            if f.suffix==".py":ast.parse(f.read_text(encoding="utf-8-sig"),filename=rel)
            elif f.suffix==".ps1":powershells.append(str(f))
        powershells += [str(SKILL/"scripts/bootstrap.ps1"),str(SKILL/"scripts/register_tasks.ps1")]
        literals=",".join(b.psquote(x) for x in powershells)
        code="$bad=@();foreach($file in @("+literals+")){$t=$null;$e=$null;$null=[Management.Automation.Language.Parser]::ParseFile($file,[ref]$t,[ref]$e);if($e.Count){$bad+=@($e|ForEach-Object {$_.Message+' '+$file})}};if($bad.Count){$bad|ConvertTo-Json;exit 1}else{'ok'}"
        self.assertEqual(b.powershell(code,log=WORK/"powershell-parse.log"),"ok")

    def test_identity_binding_requires_consistent_configs(self):
        root,p=self.fixture()
        pending=root/"project/wechat_autoreply.lulu.pending.json";value=b.load(pending);value["account_username"]="other-account";b.atomic(pending,value)
        before=(root/"project/wechat_autoreply.json").read_bytes()
        with self.assertRaises(ValueError):s.bind_config(p,"fixture-account")
        self.assertEqual((root/"project/wechat_autoreply.json").read_bytes(),before)

    def test_bind_once_and_preserve_manual_disable(self):
        root,p=self.fixture();s.bind_config(p,"fixture-account")
        for name in ("wechat_autoreply.json","wechat_autoreply.lulu.pending.json"):
            self.assertEqual(b.load(root/"project"/name)["account_username"],"fixture-account")
        path=root/"project/wechat_autoreply.json";value=b.load(path);value["enabled"]=False;b.atomic(path,value)
        with self.assertRaises(ValueError):s.bind_config(p,"fixture-account")
        self.assertFalse(b.load(path)["enabled"])
        with self.assertRaises(ValueError):s.bind_config(p,"different-account")

    def test_stop_prevents_setup_side_effects(self):
        root,p=self.fixture();state=root/"project/.wechat_codex";state.mkdir();(state/"STOP").write_text("human")
        with patch.object(s,"locked",side_effect=AssertionError("must not inspect desktop")):
            result=s.reconcile(root/"profile.json")
        self.assertEqual(result["status"],"paused")
        self.assertEqual((state/"STOP").read_text(),"human")

    def test_no_star_target_no_account_actions(self):
        root,p=self.fixture()
        spec=importlib.util.spec_from_file_location("fixture_star",root/"rdc/star-account.py")
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        class UI:
            def __getattr__(self,key):raise AssertionError("UI must not be used")
        self.assertEqual(mod.ensure_account(UI())["status"],"owner_required")

    def test_failure_checkpoint_and_retry_preserve_files(self):
        root=new_root()
        with patch.object(b,"foreign_listener",return_value=False),patch.object(b,"dependencies",side_effect=RuntimeError("fixture install failure")):
            with self.assertRaises(RuntimeError):b.install(root,prepare=True)
        self.assertEqual(b.load(root/"bootstrap-state.json")["status"],"needs_attention")
        state=root/"project/.wechat_codex";state.mkdir();(state/"STOP").write_text("new-human-stop")
        with patch.object(b,"foreign_listener",return_value=False),patch.object(b,"dependencies",side_effect=RuntimeError("fixture retry failure")):
            result=b.install(root,prepare=True)
        self.assertEqual(result["status"],"paused")
        self.assertEqual((state/"STOP").read_text(),"new-human-stop")

    def test_pong_requires_fresh_snapshot(self):
        now=1000.
        self.assertTrue(s.server_pong_fresh({"updatedAt":995.,"lastPongAgeMs":5000},now))
        self.assertFalse(s.server_pong_fresh({"updatedAt":900.,"lastPongAgeMs":1000},now))
        self.assertFalse(s.server_pong_fresh({"updatedAt":995.,"lastPongAgeMs":71000},now))
        self.assertFalse(s.server_pong_fresh({"updatedAt":1001.,"lastPongAgeMs":0},now))
        self.assertFalse(s.server_pong_fresh({"updatedAt":999.},now))

    def test_schedule_dedup_and_22_delay(self):
        root,p=self.fixture()
        class FakeDate(datetime.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,23,22,4,tzinfo=tz)
        with patch.object(s.datetime,"datetime",FakeDate):
            self.assertTrue(s.hourly_due(root,"health",range(8,23)))
            self.assertFalse(s.hourly_due(root,"health",range(8,23)))
        class EarlyDate(datetime.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,23,7,59,tzinfo=tz)
        with patch.object(s.datetime,"datetime",EarlyDate):
            self.assertFalse(s.hourly_due(root,"health",range(8,23)))

    def test_archive_traversal_rejected(self):
        root=b.claim(new_root());archive=root/"bad.zip"
        with zipfile.ZipFile(archive,"w") as z:z.writestr("../escaped.txt","bad")
        with self.assertRaises(ValueError):b.unzip_checked(archive,root/"extract")
        self.assertFalse((root/"escaped.txt").exists())

    def test_task_registration_plan_is_readonly(self):
        root,p=self.fixture()
        output=b.run(["powershell.exe","-NoProfile","-File",SKILL/"scripts/register_tasks.ps1","-ProfilePath",root/"profile.json","-Plan"],log=WORK/"task-plan.log")
        self.assertEqual({x["role"] for x in json.loads(output)},{"setup","listener","rdc","network","health"})
        self.assertNotEqual(p["task_names"]["rdc"],p["task_names"]["listener"])

    def test_port_remap_does_not_cascade_or_reset(self):
        root,p=self.fixture();p["http_port"]=21001;p["socks_port"]=21000
        original=b"a=21000;b=21001"
        self.assertEqual(b.portable_source("project/fixture.py",original,p),b"a=21001;b=21000")
        b.stage_runtime(root,p)
        self.assertEqual(b.load(root/"profile.json")["http_port"],21001)

    def test_metadata_health_reuses_fresh_result_without_model(self):
        root,p=self.fixture();health=root/"project/.wechat_codex/image-generation-health.json"
        b.atomic(health,{"ok":True,"status":"ready_without_generation_test","modelInvoked":False})
        with patch.object(s.subprocess,"run",side_effect=AssertionError("do not start process")):
            self.assertTrue(s.native_capability_ready(p))

    def test_powershell_entry_plan_no_changes(self):
        root=new_root()
        output=b.run(["powershell.exe","-NoProfile","-File",SKILL/"scripts/bootstrap.ps1","-InstallRoot",root,"-Plan"])
        self.assertEqual(json.loads(output)["mode"],"plan")
        self.assertFalse(root.exists())

    def test_unconfigured_environment_plan_does_not_write(self):
        root=new_root();result=b.install(root,plan=True)
        self.assertFalse(root.exists());self.assertEqual(result["mode"],"plan")
        self.assertIn("rdc_and_recovery_patches",result["steps"])

if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BootstrapTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"ok":result.wasSuccessful(),"testsRun":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"modelInvoked":False,"messagesSent":0,"applicationsInstalled":False,"tasksRegistered":False}
    (WORK/"test-result.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
