#!/usr/bin/env python3
"""微信鹭鹭离线管理工具；不执行生产代码、模型、网络、UI 或控制任务。"""
import argparse
import ast
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("清单路径无效")
    p = Path(value)
    if p.is_absolute() or not p.parts or any(s in (".", "..") for s in p.parts):
        raise ValueError("清单路径越界")
    if p.parts[0] not in ("project", "engine", "rdc"):
        raise ValueError("清单目录不在白名单")
    return p


def verified_sources():
    base = ROOT / "assets" / "runtime"
    manifest = read_json(ROOT / "runtime-manifest.json")
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("清单格式无效")
    expected = {}
    for entry in manifest["files"]:
        rel = safe_relative(entry["path"])
        key = rel.as_posix()
        if key.casefold() in {k.casefold() for k in expected}:
            raise ValueError("清单存在重复路径")
        path = base / rel
        if any(p.is_symlink() for p in (base, path, *path.parents)):
            raise ValueError("源码路径不能是符号链接")
        if not path.is_file() or path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
            raise ValueError("源码缺失或哈希不一致: " + key)
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=key)
        expected[key] = path
    actual = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
    if actual != set(expected):
        raise ValueError("源码目录存在清单外文件或缺失文件")
    if any(p.is_symlink() for p in base.rglob("*")):
        raise ValueError("源码目录包含符号链接")
    if not expected:
        raise ValueError("源码清单为空")
    return expected


def validate_config(config):
    issues = []
    exact = {
        "version": 2, "scope": "all_private_contacts",
        "reply_model": "gpt-6.1-sol", "reasoning_effort": "medium",
        "reply_order": "global_fifo", "max_concurrent_replies": 1,
        "interaction_mode": "desktop_ui", "pending_reply_policy": "latest_new_message",
        "replace_unsent_drafts": True,
    }
    for key, value in exact.items():
        if type(config.get(key)) is not type(value) or config.get(key) != value:
            issues.append(key + " 与本 skill 基线不一致")
    if type(config.get("enabled")) is not bool:
        issues.append("enabled 必须为布尔值")
    account = config.get("account_username", "")
    if not isinstance(account, str) or (config.get("enabled") and (not account.strip() or "@chatroom" in account)):
        issues.append("启用前必须绑定实际个人账号")
    if config.get("content_policy", {}).get("sensitive_politics") != "silent":
        issues.append("政治敏感内容必须保持静默")
    exclusions = config.get("contact_exclusions")
    if not isinstance(exclusions, dict):
        issues.append("contact_exclusions 必须为对象")
    else:
        for key in ("labels", "nicknames", "contact_ids"):
            values = exclusions.get(key)
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                issues.append("contact_exclusions." + key + " 必须为字符串列表")
    for key, field, allowed in (
        ("contact_reply_styles", "profile", {"mature_playful_nonexplicit"}),
        ("contact_reply_modes", "perspective", {"owner"}),
    ):
        entries = config.get(key, {})
        if not isinstance(entries, dict):
            issues.append(key + " 必须为对象")
            continue
        for contact, entry in entries.items():
            if (not isinstance(contact, str) or not contact.strip() or "@chatroom" in contact
                    or not isinstance(entry, dict) or type(entry.get("enabled")) is not bool
                    or entry.get(field) not in allowed):
                issues.append(key + " 存在无效绑定")
                break
    return issues


def load_profile(path):
    profile = read_json(path)
    for key in ("project_root", "engine_root", "rdc_root", "deployment_state"):
        if not isinstance(profile.get(key), str) or not Path(profile[key]).is_absolute():
            raise ValueError("profile 必须包含绝对路径: " + key)
    return profile


def fields(obj, names):
    if not isinstance(obj, dict):
        return {}
    return {key: obj[key] for key in names if key in obj}


def file_snapshot(path, now):
    p = Path(path)
    try:
        obj = read_json(p)
        stamp = obj.get("checkedAt") or obj.get("updatedAt")
        age = round(now - p.stat().st_mtime, 1)
        info = {"exists": True, "sourceTime": stamp, "fileAgeSeconds": age}
        if isinstance(stamp, str):
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    info["sourceAgeSeconds"] = round(now - parsed.timestamp(), 1)
            except ValueError:
                pass
        return obj, info
    except FileNotFoundError:
        return {}, {"exists": False}
    except (OSError, ValueError, TypeError):
        return {}, {"exists": True, "readableJson": False}


def status(profile, include_important=False):
    now = datetime.now(timezone.utc).timestamp()
    state = Path(profile["project_root"]) / ".wechat_codex"
    paths = {
        "deployment": Path(profile["deployment_state"]),
        "health": state / "lulu-health.json",
        "listener": state / "listener-status.json",
        "image": state / "image-generation-health.json",
        "rdc": Path(profile["rdc_root"]) / "connection-health.json",
        "rdcManager": Path(profile["rdc_root"]) / "status.json",
        "network": Path(profile["rdc_root"]) / "star-network-health.json",
    }
    data, sources = {}, {}
    for key, path in paths.items():
        data[key], sources[key] = file_snapshot(path, now)
        if "fileAgeSeconds" in sources[key]:
            sources[key]["olderThan75Minutes"] = sources[key].get("sourceAgeSeconds", sources[key]["fileAgeSeconds"]) > profile.get("health_stale_seconds", 4500)
    listener, health = data["listener"], data["health"]
    result = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "file_snapshot_only",
        "processIdentityVerified": False, "rpcPerformedByThisCommand": False,
        "modelInvoked": False, "uiUsed": False, "messagesSent": 0,
        "sources": sources,
        "listener": fields(listener, (
            "pid", "updatedAt", "mode", "scope", "model", "reasoningEffort",
            "contactCount", "received", "sent", "errors", "pending", "active",
            "replyQueue", "pendingReplyPolicy", "interaction",
            "imageGenerationRoutingVersion", "cacheReadGuardVersion",
            "contactStyleRoutingVersion", "statusPublisherVersion")),
        "health": fields(health, (
            "checkedAt", "mode", "modelInvoked", "testMessagesSent",
            "listenerCount", "stopExists", "uiPauseExists", "unknownSendCount",
            "rdcHealthFresh", "rdcChannelState", "rdcManagerPhase",
            "rdcAuthenticationRequired", "rdcRecoveryPending")),
        "healthIssueCount": len(health.get("issues", [])),
        "image": fields(data["image"], (
            "checkedAt", "ok", "status", "modelInvoked", "generationTested", "generatedImageCount")),
        "rdc": fields(data["rdc"], (
            "pid", "updatedAt", "channelState", "joined", "lastServerPongAt", "lastPongAt")),
        "network": fields(data["network"], (
            "checkedAt", "status", "healthy", "networkHealthy", "roundFailures", "accountStatus")),
        "monitoringKeys": sorted(data["deployment"].get("monitoring", {}).keys()),
        "pauses": {
            "STOP": (state / "STOP").exists(),
            "UI-PAUSE": (state / "UI-PAUSE").exists(),
            "STAR-WATCH-STOP": (Path(profile["rdc_root"]) / "STAR-WATCH-STOP").exists(),
        },
        "configs": {},
    }
    for name in ("wechat_autoreply.json", "wechat_autoreply.lulu.pending.json"):
        config, info = file_snapshot(Path(profile["project_root"]) / name, now)
        summary = fields(config, ("version", "enabled", "scope", "reply_model", "reasoning_effort",
                                "max_concurrent_replies", "reply_order", "interaction_mode",
                                "pending_reply_policy"))
        summary["file"] = info
        summary["checks"] = validate_config(config) if config else ["配置不可读"]
        result["configs"][name] = summary
    if include_important:
        p = state / "lulu" / "important-messages.jsonl"
        records = []
        if p.exists():
            with p.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        records.append(fields(value, ("id", "at", "createdAt", "contactName", "summary")))
        result["importantTail"] = records[-20:]
        result["notificationDedupChecked"] = False
    return result


def stage(destination, profile):
    sources = verified_sources()
    dest = Path(destination)
    if not dest.is_absolute():
        raise ValueError("暂存目标必须是绝对路径")
    dest = dest.resolve()
    protected = [ROOT] + [Path(profile[k]).resolve() for k in ("project_root", "engine_root", "rdc_root")]
    for p in protected:
        if dest == p or dest in p.parents or p in dest.parents:
            raise ValueError("暂存目标不能覆盖生产、skill 或它们的父子目录")
    if any(p.is_symlink() for p in (dest, *dest.parents)):
        raise ValueError("暂存目标不能含符号链接")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError("暂存目标必须是空目录")
    dest.mkdir(parents=True, exist_ok=True)
    for rel, source in sources.items():
        target = dest / safe_relative(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("xb") as out:
            shutil.copyfileobj(src, out)
    example = dest / "project" / "wechat_autoreply.example.json"
    with example.open("x", encoding="utf-8") as out:
        json.dump(read_json(ROOT / "assets" / "config.template.json"), out, ensure_ascii=False, indent=2)
    with (dest / "STAGED-NOT-ACTIVATED.json").open("x", encoding="utf-8") as out:
        json.dump({"activated": False, "files": len(sources), "source": str(ROOT)}, out, ensure_ascii=False)
    return {"ok": True, "destination": str(dest), "files": len(sources), "activated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd_status = sub.add_parser("status")
    cmd_status.add_argument("--profile", default=str(ROOT / "assets" / "local-profile.json"))
    cmd_status.add_argument("--include-important", action="store_true")
    sub.add_parser("verify")
    cmd_config = sub.add_parser("validate-config")
    cmd_config.add_argument("--config", required=True)
    cmd_stage = sub.add_parser("stage")
    cmd_stage.add_argument("--destination", required=True)
    cmd_stage.add_argument("--profile", default=str(ROOT / "assets" / "local-profile.json"))
    args = parser.parse_args()
    try:
        if args.command == "verify":
            values = verified_sources()
            emit({"ok": True, "files": len(values), "bytes": sum(p.stat().st_size for p in values.values()),
                  "runtimeExecuted": False})
        elif args.command == "status":
            emit(status(load_profile(args.profile), args.include_important))
        elif args.command == "validate-config":
            issues = validate_config(read_json(args.config))
            emit({"ok": not issues, "issues": issues, "fileModified": False})
            return 1 if issues else 0
        else:
            emit(stage(args.destination, load_profile(args.profile)))
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, AttributeError) as exc:
        emit({"ok": False, "error": str(exc), "productionActivated": False})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
