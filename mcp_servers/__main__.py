"""python -m mcp_servers <case_mgmt|kyc_profile|txn_history|screening> [--port 8000]"""

import argparse

from mcp_servers.servers import SERVERS, build_server

parser = argparse.ArgumentParser(prog="python -m mcp_servers")
parser.add_argument("name", choices=sorted(SERVERS))
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
build_server(args.name).run(transport="http", host=args.host, port=args.port, stateless_http=True)
