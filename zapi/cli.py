"""Newman-style command line: `zapi-cli run collection.json -e env.json -d data.csv -n 3 --reporters cli,json`."""
import argparse
import json
import sys

from . import APP_NAME, VERSION, importers, runner

USAGE = f"""{APP_NAME} {VERSION} command line

  zapi-cli run COLLECTION [options]
  zapi-cli load COLLECTION [load options]

Runs a collection (Postman v2.x, OpenAPI 3 or a Zapi export) without opening a window. The exit code is 0 when every
request and test passed, 1 when something failed and 2 when the run could not start.

Options:
  -e, --environment FILE       environment file (Postman or Zapi export)
  -g, --globals FILE           globals file
  -d, --iteration-data FILE    CSV or JSON data file; one iteration per row
  -n, --iteration-count N      iterations when there is no data file (default 1)
      --folder NAME            run only this folder
      --delay-request MS       wait between requests
      --timeout-request MS     per-request timeout (default 30000)
      --bail                   stop at the first failure
  -k, --insecure               do not verify SSL certificates
      --proxy URL              send requests through this proxy ("none" = never use a proxy)
      --cert FILE              client certificate (PEM)
      --key FILE               private key for --cert (if it is not inside the certificate file)
      --cacert FILE            CA bundle to trust instead of the system store
      --no-follow-redirects
      --env-var NAME=VALUE     set / override an environment variable (repeatable)
      --global-var NAME=VALUE  set / override a global variable (repeatable)
      --reporters LIST         comma separated: cli, json, junit (default cli)
      --reporter-json-export FILE
      --reporter-junit-export FILE
      --export-environment FILE   write the environment after the run (script changes included)
      --silent                 no console output

Load test (zapi-cli load): many virtual users run the collection's requests in a loop. Only test servers you own or have
permission to test. The exit code is 1 when a threshold below is exceeded.
      --vus N                  virtual users (default 10, at most 500)
      --duration SECONDS       how long to run (default 30)
      --profile NAME           fixed (default), ramp or spike
      --think-time MS          pause between a user's requests
      --scripts                also run pre-request and test scripts (much slower)
      --max-error-rate PCT     fail when more than this percentage of requests fail
      --max-p95 MS             fail when the 95th percentile response time is above this
      --json-export FILE       write the full report as JSON
      --csv-export FILE        write one row per second (requests, average ms, errors, users)
      (also: -e -g -d --folder -k --proxy --cert --key --cacert --timeout-request --env-var --global-var --silent)
"""


def _pairs(items):
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise ValueError(f"Expected NAME=VALUE, got '{it}'")
        out[k] = v
    return out


def _parser():
    p = argparse.ArgumentParser(prog="zapi-cli", add_help=False)
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", add_help=False)
    r.add_argument("collection")
    r.add_argument("-e", "--environment")
    r.add_argument("-g", "--globals")
    r.add_argument("-d", "--iteration-data")
    r.add_argument("-n", "--iteration-count", type=int, default=1)
    r.add_argument("--folder", default="")
    r.add_argument("--delay-request", type=int, default=0)
    r.add_argument("--timeout-request", type=int, default=30000)
    r.add_argument("--bail", action="store_true")
    r.add_argument("-k", "--insecure", action="store_true")
    r.add_argument("--proxy", default="")
    r.add_argument("--cert", default="")
    r.add_argument("--key", default="")
    r.add_argument("--cacert", default="")
    r.add_argument("--no-follow-redirects", action="store_true")
    r.add_argument("--env-var", action="append")
    r.add_argument("--global-var", action="append")
    r.add_argument("--reporters", default="cli")
    r.add_argument("--reporter-json-export")
    r.add_argument("--reporter-junit-export")
    r.add_argument("--export-environment")
    r.add_argument("--silent", action="store_true")
    l = sub.add_parser("load", add_help=False)
    l.add_argument("collection")
    l.add_argument("-e", "--environment")
    l.add_argument("-g", "--globals")
    l.add_argument("-d", "--iteration-data")
    l.add_argument("--folder", default="")
    l.add_argument("--vus", type=int, default=10)
    l.add_argument("--duration", type=int, default=30)
    l.add_argument("--profile", default="fixed")
    l.add_argument("--think-time", type=int, default=0)
    l.add_argument("--scripts", action="store_true")
    l.add_argument("--max-error-rate", type=float)
    l.add_argument("--max-p95", type=int)
    l.add_argument("--json-export")
    l.add_argument("--csv-export")
    l.add_argument("--timeout-request", type=int, default=30000)
    l.add_argument("-k", "--insecure", action="store_true")
    l.add_argument("--proxy", default="")
    l.add_argument("--cert", default="")
    l.add_argument("--key", default="")
    l.add_argument("--cacert", default="")
    l.add_argument("--no-follow-redirects", action="store_true")
    l.add_argument("--env-var", action="append")
    l.add_argument("--global-var", action="append")
    l.add_argument("--silent", action="store_true")
    return p


def _load(args):
    import time

    from . import loadtest, variables
    from .loadtest import hosts_of, with_collection_variables
    try:
        kind, coll = importers.import_file(args.collection)
        if kind != "collection":
            raise ValueError("That file is an environment, not a collection.")
        scopes = {"environment": runner.env_scope_from_file(args.environment) if args.environment else {},
                  "globals": runner.env_scope_from_file(args.globals) if args.globals else {}, "collection": {}}
        scopes["environment"].update(_pairs(args.env_var))
        scopes["globals"].update(_pairs(args.global_var))
        data = runner.load_iteration_data(args.iteration_data) if args.iteration_data else None
        profile = {"fixed": "Fixed", "ramp": "Ramp up", "spike": "Spike"}.get(args.profile.lower())
        if profile is None:
            raise ValueError("--profile must be fixed, ramp or spike.")
    except (OSError, ValueError, importers.ImportError_) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    items = loadtest.build_items(coll, args.folder)
    if not items:
        print("error: there are no requests to run.", file=sys.stderr)
        return 2
    scopes = with_collection_variables(coll, scopes)
    cfg = loadtest.LoadConfig(profile, args.vus, args.duration, args.think_time, args.scripts).clamp()
    opts = {"verify_ssl": not args.insecure, "follow_redirects": not args.no_follow_redirects,
            "timeout": args.timeout_request / 1000, "proxy": args.proxy,
            "client_cert": (args.cert, args.key) if args.cert else None, "ca_file": args.cacert}
    values = variables.merge_scopes(scopes["globals"], scopes["collection"], scopes["environment"])
    if not args.silent:
        print(f"{APP_NAME} load test: {coll['name']}  ({len(items)} requests, {cfg.profile}, up to {cfg.vus} users, "
              f"{cfg.duration_s} s)\ntarget: {', '.join(hosts_of(items, values)) or '(unknown)'}\n", flush=True)
    test = loadtest.LoadTest(items, scopes, opts, cfg, data)
    test.start()
    last = 0
    try:
        while test.running:
            time.sleep(0.25)
            if not args.silent and time.perf_counter() - last >= 5:
                last = time.perf_counter()
                st = test.snapshot()
                print(f"  {st.elapsed:5.0f}s  {st.requests:>7} requests  {st.rps:7.1f}/s  avg {st.avg_ms:5.0f} ms  "
                      f"p95 {st.p95:5} ms  errors {st.error_rate:4.1f}%", flush=True)
    except KeyboardInterrupt:
        test.stop()
        test.wait(10)
    stats = test.snapshot()
    if not args.silent:
        print("\n" + loadtest.format_summary(coll["name"], cfg, stats))
    try:
        if args.json_export:
            with open(args.json_export, "w", encoding="utf-8") as f:
                f.write(loadtest.report_json(coll["name"], cfg, stats))
        if args.csv_export:
            with open(args.csv_export, "w", encoding="utf-8", newline="") as f:
                f.write(loadtest.report_csv(stats))
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    failed = []
    if args.max_error_rate is not None and stats.error_rate > args.max_error_rate:
        failed.append(f"error rate {stats.error_rate:.1f}% is above {args.max_error_rate}%")
    if args.max_p95 is not None and stats.p95 > args.max_p95:
        failed.append(f"p95 {stats.p95} ms is above {args.max_p95} ms")
    if stats.requests == 0:
        failed.append("no request completed")
    for msg in failed:
        print(f"threshold failed: {msg}", file=sys.stderr)
    return 1 if failed else 0


def main(argv):
    """argv excludes the program name. Returns the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"{APP_NAME} {VERSION}")
        return 0
    if argv[0] == "--data-dir":                      # where this copy keeps its workspace (portable builds: beside the exe)
        from . import config
        print(config.data_dir())
        return 0
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        print(USAGE, file=sys.stderr)
        return 2
    if args.cmd == "load":
        return _load(args)
    if args.cmd != "run":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        kind, coll = importers.import_file(args.collection)
        if kind != "collection":
            raise ValueError("That file is an environment, not a collection.")
        scopes = {"environment": runner.env_scope_from_file(args.environment) if args.environment else {},
                  "globals": runner.env_scope_from_file(args.globals) if args.globals else {}, "collection": {}}
        scopes["environment"].update(_pairs(args.env_var))
        scopes["globals"].update(_pairs(args.global_var))
        data = runner.load_iteration_data(args.iteration_data) if args.iteration_data else None
    except (OSError, ValueError, importers.ImportError_) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    reporters = {r.strip() for r in args.reporters.split(",") if r.strip()}
    show = "cli" in reporters and not args.silent
    if show:
        print(f"{APP_NAME} run: {coll['name']}  ({len(runner.select_requests(coll, args.folder))} requests"
              f"{', ' + str(len(data)) + ' iterations' if data else ''})\n")
    opts = {"verify_ssl": not args.insecure, "follow_redirects": not args.no_follow_redirects,
            "timeout": args.timeout_request / 1000, "proxy": args.proxy,
            "client_cert": (args.cert, args.key) if args.cert else None, "ca_file": args.cacert}

    def on_step(step):
        if show:
            print(runner.format_step_cli(step) + "\n", flush=True)

    summary = runner.run_collection(coll, scopes, opts, iterations=args.iteration_count, data=data,
                                    delay_ms=args.delay_request, bail=args.bail, folder=args.folder, on_step=on_step)
    if show:
        print(runner.format_summary_cli(summary))
    try:
        if "json" in reporters or args.reporter_json_export:
            text = runner.report_json(summary)
            if args.reporter_json_export:
                with open(args.reporter_json_export, "w", encoding="utf-8") as f:
                    f.write(text)
            elif not args.silent:
                print(text)
        if "junit" in reporters or args.reporter_junit_export:
            text = runner.report_junit(summary)
            if args.reporter_junit_export:
                with open(args.reporter_junit_export, "w", encoding="utf-8") as f:
                    f.write(text)
            elif not args.silent:
                print(text)
        if args.export_environment:
            with open(args.export_environment, "w", encoding="utf-8") as f:
                json.dump({"name": "exported", "values": [
                    {"key": k, "value": v, "enabled": True} for k, v in summary.scopes["environment"].items()]},
                    f, indent=2)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0 if summary.success else 1
