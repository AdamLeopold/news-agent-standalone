import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request

from .config import ROOT, DATA_DIR, PORT
from .store import Store, dumps

RUNTIME = ROOT / ".runtime"


def alive(pid):
    try:
        os.kill(pid,0)
        state = subprocess.run(["ps","-p",str(pid),"-o","stat="],capture_output=True,text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")
    except (OSError,ValueError):
        return False


def health_url(url,timeout=2):
    try:
        with urllib.request.urlopen(url,timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return None


def start():
    RUNTIME.mkdir(exist_ok=True)
    pidfile = RUNTIME / "app.pid"
    if pidfile.exists() and alive(int(pidfile.read_text())):
        health = health_url(f"http://127.0.0.1:{PORT}/api/health")
        if health and health.get("server_pid") == int(pidfile.read_text()):
            print(f"Already running: http://127.0.0.1:{PORT}")
            return
        raise RuntimeError("Managed process is not serving the expected health endpoint; inspect .runtime/app.log")
    if health_url(f"http://127.0.0.1:{PORT}/api/health"):
        raise RuntimeError(f"Port {PORT} already has a service without a matching managed PID; refusing to start a duplicate")
    if not health_url("http://127.0.0.1:11434/api/tags"):
        model_log = open(RUNTIME/"ollama.log","ab")
        env = {**os.environ,"OLLAMA_HOST":"127.0.0.1:11434","OLLAMA_NUM_PARALLEL":"1","OLLAMA_MAX_LOADED_MODELS":"1"}
        model = subprocess.Popen(["ollama","serve"],stdout=model_log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        (RUNTIME/"ollama.pid").write_text(str(model.pid))
        for _ in range(60):
            if health_url("http://127.0.0.1:11434/api/tags"):
                break
            if model.poll() is not None:
                raise RuntimeError("Ollama did not start; inspect .runtime/ollama.log")
            time.sleep(0.5)
        else:
            raise RuntimeError("Ollama startup timed out")
    app_log = open(RUNTIME/"app.log","ab")
    app = subprocess.Popen([sys.executable,"-m","uvicorn","ttf_news.api:app","--host","127.0.0.1","--port",str(PORT),"--timeout-graceful-shutdown","5"],cwd=ROOT,stdout=app_log,stderr=subprocess.STDOUT,start_new_session=True)
    pidfile.write_text(str(app.pid))
    # Keep the collector running with the display off; this is a process-scoped assertion,
    # not a permanent macOS setting. Explicit sleep, lid closure and shutdown still suspend collection.
    if sys.platform=='darwin':
        try:
            awake_log=open(RUNTIME/'awake.log','ab')
            awake=subprocess.Popen(['caffeinate','-i','-w',str(app.pid)],stdout=awake_log,stderr=subprocess.STDOUT,start_new_session=True)
            (RUNTIME/'awake.pid').write_text(str(awake.pid))
        except OSError:
            pass
    for _ in range(60):
        if app.poll() is not None:
            raise RuntimeError("Backend failed to start; inspect .runtime/app.log")
        health = health_url(f"http://127.0.0.1:{PORT}/api/health",timeout=3)
        if health and health.get("server_pid") == app.pid:
            print(f"Running: http://127.0.0.1:{PORT}")
            print(f"Logs: {RUNTIME}")
            return
        time.sleep(0.5)
    raise RuntimeError("Backend startup timed out")


def stop(with_model=False):
    names = ["app"]+(["ollama"] if with_model else [])
    for name in names:
        path = RUNTIME/f"{name}.pid"
        if not path.exists():
            continue
        pid = int(path.read_text())
        if alive(pid):
            args = subprocess.run(["ps","-p",str(pid),"-o","args="],capture_output=True,text=True).stdout
            expected = "ttf_news.api:app" if name=="app" else "ollama serve"
            if expected not in args:
                raise RuntimeError(f"PID {pid} is not the managed {name} process; refusing to stop it")
            os.kill(pid,signal.SIGTERM)
            for _ in range(50):
                if not alive(pid):
                    break
                time.sleep(0.2)
            if alive(pid):
                # Open SSE connections can delay graceful exit. Only terminate our verified process.
                args = subprocess.run(["ps","-p",str(pid),"-o","args="],capture_output=True,text=True).stdout
                if expected not in args:
                    raise RuntimeError("Managed PID changed identity during shutdown")
                os.kill(pid,signal.SIGKILL)
                for _ in range(25):
                    if not alive(pid):
                        break
                    time.sleep(0.2)
            if alive(pid):
                raise RuntimeError(f"Managed {name} process did not stop")
        path.unlink(missing_ok=True)
    print("Stopped managed processes")


def export(store,out_dir):
    from .export import render_export
    out = Path(out_dir or DATA_DIR/"exports")
    out.mkdir(parents=True,exist_ok=True)
    for name in ("news.csv","news.jsonl","daily.csv","daily.jsonl","events.csv","events.jsonl","prices.csv","prices.jsonl","legacy_daily.jsonl","analyses.jsonl","coverage.json"):
        content,_ = render_export(store,name)
        (out/name).write_bytes(content)
    print(out)


def main():
    parser = argparse.ArgumentParser(description="Local TTF news product")
    parser.add_argument("command",choices=["start","stop","status","import-legacy","collect","backfill","prices","export","backup","report"])
    parser.add_argument("--start-date",default="2024-05-15")
    parser.add_argument("--source",action="append")
    parser.add_argument("--output")
    parser.add_argument("--with-model",action="store_true")
    args = parser.parse_args()
    if args.command=="start":
        start(); return
    if args.command=="stop":
        stop(args.with_model); return
    if args.command=="status":
        print(dumps(health_url(f"http://127.0.0.1:{PORT}/api/health") or {"status":"not_running"})); return
    store = Store()
    if args.command=="import-legacy":
        from .legacy import import_legacy
        print(dumps(import_legacy(store)))
    elif args.command in ("collect","backfill"):
        from .ingest import poll_sources
        result = asyncio.run(poll_sources(store,historical=args.command=="backfill",start_date=args.start_date,source_ids=args.source))
        from .context import update_event_memory
        for item in result.get("created",[])+result.get("revised",[]):
            update_event_memory(store,item)
        print(dumps({k:v for k,v in result.items() if k not in ("created","revised")}))
    elif args.command=="prices":
        from .prices import refresh_prices
        print(dumps(asyncio.run(refresh_prices(store))))
    elif args.command=="export":
        export(store,args.output)
    elif args.command=="backup":
        from .calendar import utcnow
        dest = Path(args.output or DATA_DIR/"backups"/f"ttf-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.sqlite3")
        dest.parent.mkdir(parents=True,exist_ok=True)
        with store.connection() as source, sqlite3.connect(dest) as target:
            source.backup(target)
        print(dest)
    elif args.command=="report":
        from .evaluation import monitoring_report
        data = {"coverage":store.coverage(),"monitoring":monitoring_report(store)}
        if args.output:
            Path(args.output).write_text(json.dumps(data,ensure_ascii=False,indent=2))
        else:
            print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
