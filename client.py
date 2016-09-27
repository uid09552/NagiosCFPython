import argparse, sys, requests, jwt, datetime, time, json, os.path
from datetime import timedelta
from cloudfoundry_client.client import CloudFoundryClient

parser = argparse.ArgumentParser()
parser.add_argument("--api", help="CF URL", required=True)
parser.add_argument("--user", help="CF UserName", required=True)
parser.add_argument("--password", help="CF Password", required=True)
parser.add_argument("--Org", help="CF Organisation")
parser.add_argument("--app", help="CF App")
parser.add_argument("--action", help="available actions: appstats", required=True)
parser.add_argument("--appguid", help="app guid")
parser.add_argument("--appname")
parser.add_argument("--uaa", help="uaa api")
parser.add_argument("--warnOnCrashEventSeconds", help="look back x seconds for crash events", type=int)
parser.add_argument("--disableWarnOnCrash", help="set parameter for disabling")
parser.add_argument("--proxy", help="proxy configuration")
parser.add_argument("--spaceguid", help="spaceguid to be verified", required=True)
_args = parser.parse_args()


# api
GET_APPS = "/v2/apps"
GET_APP_STATS_DETAILS = "/v2/apps/<<appguid>>/stats"
GET_APP_STATS = "/v2/apps/<<appguid>>/summary"
GET_INFO = "/v2/info"
CHECK_SERVICE = "/check_token"
GET_APP_EVENTS = "/v2/events?q=type:app.crash&q=actee:<<appguid>>&order-direction=desc&q=timestamp><<timestamp>>"


BYTE_TO_MB = 1024 * 1024
SECONDS_TO_HOURS = 60*60



if _args.warnOnCrashEventSeconds is not None:
    seconds_from_last_crash_event = int(_args.warnOnCrashEventSeconds)  # 10 min
else:
    seconds_from_last_crash_event = 600  # 10 min


def get_token(user, password, host, proxy=None, reset_token="no"):
    token_file = None
    if os.path.isfile('.token'):
        token_file = open('.token', 'r')
    token = None

    if token_file is not None and reset_token != "yes":
        try:
            token = token_file.read()
            token_file.close()
            if token is not None and token != '':
                decoded_token = jwt.decode(token, verify=False)
                exp = decoded_token['exp']
                present = int(time.time())
                if exp < present:
                    token = None
        except Exception, e:
            token = None

    if token is None or reset_token == "yes":
        proxyDic = None
        if proxy is not None:
            proxyDic = dict(http=proxy, https=proxy)
        client = CloudFoundryClient(host, proxy=proxyDic)
        client.init_with_user_credentials(user, password)
        token = client.refresh_token
        token_file = open('.token', 'w')
        token_file.write(token)
        token_file.close()

    return token


def get_cf_header(token):
    return {"Authorization": "bearer " + token}


def get_parsed_url(url, args):
    if args.appguid is not None:
        url = url.replace("<<appguid>>", args.appguid)
    return url


def get_cf_data(url, args):
    token = get_token(args.user, args.password, args.api, args.proxy)
    headers = get_cf_header(token)
    url=get_parsed_url(url, args)
    proxyDic = None
    if args.proxy is not None:
        proxyDic={
            "http": args.proxy,
            "https": args.proxy
        }
    r = requests.get(args.api + url, headers=headers, proxies=proxyDic)
    if r.status_code == 401:
        token = get_token(args.user, args.password, args.api, args.proxy, "yes")
        headers = get_cf_header(token)
        r = requests.get(args.api + url, headers=headers)
    return r.content


def get_utc_parsed_time(elapsed_time):
    return str((datetime.datetime.utcnow() - timedelta(seconds=elapsed_time)).isoformat()) + "Z"


def get_app_stats(args):
    crash_events_last_minutes = False
    app_summary_json = json.loads(get_cf_data(GET_APP_STATS, args))
    utc_time = get_utc_parsed_time(seconds_from_last_crash_event)

    if args.disableWarnOnCrash is None:
        appEvents = json.loads(get_cf_data(GET_APP_EVENTS.replace("<<timestamp>>", utc_time), args))
        if len(appEvents["resources"]) > 0:
            crash_events_last_minutes = True

    perf_data = ""
    state = app_summary_json["state"]
    memory = app_summary_json["memory"]
    instances = app_summary_json["instances"]
    running_instances = 0
    disk_quota = app_summary_json["disk_quota"]
    max_memory = 0
    max_cpu = 0
    max_disk = 0
    min_uptime = sys.maxint
    max_uptime = 0

    if state == "STARTED":
        app_details_json = json.loads(get_cf_data(GET_APP_STATS_DETAILS, args))
        for app_detail in app_details_json:
            details = app_details_json[app_detail]
            if details["state"] == "RUNNING":
                min_uptime = min([min_uptime, details["stats"]["uptime"]])
                max_uptime = max([max_uptime, details["stats"]["uptime"]])
                max_cpu = max([int(details["stats"]["usage"]["cpu"] * 100), max_cpu])
                max_memory = max ([int(details["stats"]["usage"]["mem"]), max_memory])
                max_disk = max([int(details["stats"]["usage"]["disk"]), max_disk])
                running_instances += 1

        if running_instances > 0:
            perf_data = " | max_memory=" + str(max_memory / (BYTE_TO_MB)) + "MB;" + str(memory * 0.85) + ";" + \
                       str(memory * 0.9) + ";;;" + " max_cpu=" + str(max_cpu) + ";" + str(95) + ";" + str(100) + ";;;" + \
                       " max_disk=" + str(max_disk / (BYTE_TO_MB)) + "MB;" + str(disk_quota * 0.9) + ";" + \
                       str(disk_quota * 0.9) + ";;; min_uptime=" + str(min_uptime / SECONDS_TO_HOURS) + ";10;10;;; " \
                                                                                           " max_uptime=" + str(
                max_uptime / SECONDS_TO_HOURS) + ";10;10;;; instances=" + str(running_instances) + ";0;0;;;"

    if running_instances == 0 and state=="STARTED":
        nagios_state = 2  # Critical
        status_text = "Critical - no running instances"
    elif running_instances < instances:
        nagios_state = 1  # Warning
        status_text = "Warning - not all instances are running"
    elif running_instances == instances:
        if crash_events_last_minutes:
            nagios_state = 1
            status_text = "Warning - crash events available"
        else:
            nagios_state = 0
            status_text = "OK"
    else:
        nagios_state = 3  # Unknown
        status_text = "Unknown - Unknown state"
    print (status_text + perf_data)
    sys.exit(nagios_state)


def main(args):
    if (args.action == "appstats"):
        get_app_stats(args)

if _args.appname is not None and _args.spaceguid is not None:
    apps = json.loads(get_cf_data(GET_APPS+"?q=name:"+_args.appname,_args))
    app = filter(lambda x: x["entity"]["name"] == _args.appname and x["entity"]["space_guid"] == _args.spaceguid, apps.get("resources"))
    if len(app) > 0:
        _args.appguid = app[0]["metadata"]["guid"]

if __name__ == "__main__":
    main(_args)  # TODO EIgenes Modul auslagern

class CFClient:
    args=None
    def __init__(self, arguments):
        _args=arguments

    def getUTCParsedTime(elapsedTime):
        return str((datetime.datetime.utcnow() - timedelta(seconds=elapsedTime)).isoformat()) + "Z"




class NagiosState:
    perfData=[]

