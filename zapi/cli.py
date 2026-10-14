"""Newman-style command line: `zapi-cli run collection.json -e env.json -d data.csv -n 3 --reporters cli,json`."""
import argparse
import json
import sys

from . import APP_NAME, VERSION, importers, runner

USAGE = f"""{APP_NAME} {VERSION} command line

  zapi-cli run COLLECTION [options]

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
    return p


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
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        print(USAGE, file=sys.stderr)
        return 2
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
