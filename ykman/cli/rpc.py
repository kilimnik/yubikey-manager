import cmd
import json
import click
import sys
from threading import Thread
from queue import Queue
from ..rpc import run_rpc
from ..rpc.base import encode_bytes
import logging

logger = logging.getLogger(__name__)


def red(value):
    return f"\u001b[31;1m{value}\u001b[0m"


def green(value):
    return f"\u001b[32;1m{value}\u001b[0m"


def yellow(value):
    return f"\u001b[33;1m{value}\u001b[0m"


def cyan(value):
    return f"\u001b[36;1m{value}\u001b[0m"


class RpcShell(cmd.Cmd):
    def __init__(self, cmd_queue, result_queue):
        super().__init__()
        self._cmds = cmd_queue
        self._results = result_queue
        self._path = []
        self._node = None
        self.do_cd(None)

    @property
    def prompt(self):
        return "/" + "/".join(self._path) + "> "

    def resolve_path(self, line):
        if line:
            parts = line.split("/")
            if parts[0]:
                parts = self._path + parts
            else:
                parts.pop(0)
            while ".." in parts:
                pos = parts.index("..")
                parts.pop(pos - 1)
                parts.pop(pos - 1)
        else:
            parts = self._path + [""]
        return parts

    def completepath(self, text, nodes_only=False):
        target = self.resolve_path(text)
        cmd = target.pop() if target else ""
        node = self.get_node(target)
        if node:
            names = [n + "/" for n in node.get("children", [])]
            if not nodes_only:
                actions = node.get("actions", [])
                if "get" in actions:
                    actions.remove("get")
                names += actions
            res = [n for n in names if n.startswith(cmd)]
            return res
        return []

    def completedefault(self, cmd, text, *args):
        return self.completepath(text)

    def completenames(self, cmd, text, *ignored):
        return self.completepath(text)

    def emptyline(self):
        self.do_ls(None)

    def get_node(self, target):
        logger.debug("sending get: %r", target)
        self._cmds.put({"kind": "command", "action": "get", "target": target})
        result = self._results.get()
        logger.debug("got info: %r", result)
        kind = result["kind"]
        if kind == "success":
            return result
        elif kind == "error":
            status = result["status"]
            print(red(f"{status.upper()}: {result['body']}"))
        else:
            print(red(f"Invalid response: {result}"))

    def do_quit(self, args):
        self._cmds.put(None)
        return True

    def do_cd(self, args):
        if args:
            target = self.resolve_path(args)
            if target and not target[-1]:
                target.pop()
        else:
            target = []
        logger.debug("Get info for %r", target)
        if self.get_node(target):
            self._path = target
            logger.debug("set path %r", target)

    def complete_cd(self, cmd, text, *args):
        return self.completepath(text[3:], True)

    def do_ls(self, args):
        self._cmds.put({"kind": "command", "action": "get", "target": self._path})
        result = self._results.get()
        kind = result["kind"]
        if kind == "success":
            self._node = result["body"]
            data = self._node.get("data", None)
            if data:
                for k, v in data.items():
                    print(yellow(f"{k}: {v}"))

            for c, c_data in self._node.get("children", {}).items():
                print(green(f"{c}/"))
                if c_data:
                    for k, v in c_data.items():
                        print(yellow(f"  {k}: {v}"))

            for name in self._node.get("actions", []):
                if name != "get":  # Don't show get, always available
                    print(cyan(f"{name}"))
        elif kind == "error":
            status = result["status"]
            print(red(f"{status.upper()}: {result['body']}"))
        else:
            print(red(f"Invalid response: {result}"))

    def default(self, line):
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            try:
                args = json.loads(parts[1])
                if not isinstance(args, dict):
                    logger.error("Argument must be a JSON Object")
                    return
            except json.JSONDecodeError as e:
                logger.error("Error decoding JSON.", exc_info=e)
                return
        else:
            args = {}
        target = self.resolve_path(parts[0])
        action = target.pop()
        cmd = {
            "kind": "command",
            "action": action or "get",
            "target": target,
            "body": args,
        }
        self._cmds.put(cmd)

        while True:
            result = self._results.get()
            kind = result["kind"]

            if kind == "signal":
                print(cyan(f"{result['status']}: {result.get('body', None)}"))
            else:
                break

        if kind == "success":
            body = result.get("body", None)
            if body:
                print(yellow(json.dumps(body)))
        elif kind == "error":
            print(red(f"{result['status']}: {result['message']}"))
            body = result.get("body", None)
            if result:
                print(red(json.dumps(body)))
        else:
            print(red(f"Invalid response: {result}"))

    def do_EOF(self, args):
        return True


def _json_encode(value):
    if isinstance(value, bytes):
        return encode_bytes(value)
    raise TypeError(type(value))


@click.command()
@click.option("-s", "--shell", is_flag=True)
def rpc(shell):
    if shell:
        cmd_queue = Queue(1)  # type: ignore

        def send(data):
            # Make sure JSON encodable
            data = json.loads(json.dumps(data, default=_json_encode))
            result_queue.put(data)

        result_queue = Queue(1)  # type: ignore

        def recv():
            # Make sure JSON encodeable
            request = cmd_queue.get()
            return json.loads(json.dumps(request))

        rpc_thread = Thread(target=run_rpc, args=(send, recv))
        rpc_thread.start()

        shell = RpcShell(cmd_queue, result_queue)
        shell.cmdloop()
        cmd_queue.put(None)
    else:

        def send(data):
            json.dump(data, sys.stdout, default=_json_encode)
            sys.stdout.write("\n")
            sys.stdout.flush()

        def recv():
            line = sys.stdin.readline()
            if line:
                return json.loads(line.strip())
            return None

        run_rpc(send, recv)
