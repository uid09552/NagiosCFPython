import argparse, sys, requests, jwt, datetime, time, json
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
_args = parser.parse_args()
# api
GETAPPS = "/v2/apps"
GETAPPSTATSDETAILS = "/v2/apps/<<appguid>>/stats"
GETAPPSTATS = "/v2/apps/<<appguid>>/summary"
GETINFO = "/v2/info"
CHECKSERVICE = "/check_token"
GETAPPEVENTS = "/v2/events?q=type:app.crash&q=actee:<<appguid>>&order-direction=desc&q=timestamp><<timestamp>>"
BYTE_TO_MB = 1024 * 1024
SECONDS_TO_MINUTES=60

SecondsFromLastCrashEvent = 600 #10 min


def getToken(user, password, host, resetToken="no"):
    file = open('.token', 'r')
    token = None
    if (file != None and resetToken != "yes"):
        try:
            token = file.read()
            file.close()
            if token != None and token != '':
                decodedtoken = jwt.decode(token, verify=False)
                exp = decodedtoken['exp']
                present = int(time.time())
                if exp < present:
                    token = None
        except Exception, e:
            token = None

    if token == None or resetToken == "yes":
        client = CloudFoundryClient(host)
        client.init_with_user_credentials(user, password)
        token = client.refresh_token
        file = open('.token', 'w')
        file.write(token)
        file.close()
    return token


def getCFHeader(token):
    return {"Authorization": "bearer " + token}


def getCFData(url, args):
    token = getToken(args.user, args.password, args.api)
    headers = getCFHeader(token)
    host = args.api
    if (args.appguid != None):
        url = url.replace("<<appguid>>", args.appguid)
    r = requests.get(host + url, headers=headers)

    if r.status_code == 401:
        token = getToken(args.user, args.password, args.api, "yes")
        headers = getCFHeader(token)
        r = requests.get(host + url, headers=headers)

    return r.content


def getAppStats(args):
    crashEventsLastMinutes = False
    appSummaryJson = json.loads(getCFData(GETAPPSTATS, args))
    utcTime = str((datetime.datetime.utcnow() - timedelta(seconds=SecondsFromLastCrashEvent)).isoformat()).replace(" ",
                                                                                                                   "") + "Z"
    appEvents = json.loads(getCFData(GETAPPEVENTS.replace("<<timestamp>>", utcTime), args))
    if len(appEvents["resources"]) > 0:
        crashEventsLastMinutes = True

    perfData = ""
    statusText = "Unknown - not set"
    state = appSummaryJson["state"]
    memory = appSummaryJson["memory"]
    instances = appSummaryJson["instances"]
    runningInstances = 0
    disk_quota = appSummaryJson["disk_quota"]
    maxMemory = 0
    maxCPU = 0
    maxDisk = 0
    minUptime = 9999999999
    maxUptime = 0

    if state == "STARTED":
        appDetailsJson = json.loads(getCFData(GETAPPSTATSDETAILS, args))
        for appDetail in appDetailsJson:
            details = appDetailsJson[appDetail]
            if details["state"] == "RUNNING":
                uptime = details["stats"]["uptime"]
                if minUptime > uptime:
                    minUptime = uptime
                if maxUptime < uptime:
                    maxUptime = uptime
                cpu = int(details["stats"]["usage"]["cpu"] * 100)
                mem = details["stats"]["usage"]["mem"]
                disk = details["stats"]["usage"]["disk"]
                if cpu > maxCPU:
                    maxCPU = cpu
                if mem > maxMemory:
                    maxMemory = mem
                if disk > maxDisk:
                    maxDisk = disk

                runningInstances = runningInstances + 1

        if runningInstances > 0:
            perfData = " | maxMemory=" + str(maxMemory / (BYTE_TO_MB)) + "MB;" + str(memory * 0.85) + ";" + \
                       str(memory * 0.9) + ";;;" + "maxCPU=" + str(maxCPU) + ";" + str(95) + ";" + str(100) + ";;;" + \
                       ", maxDisk=" + str(maxDisk / (BYTE_TO_MB)) + "MB;" + str(disk_quota * 0.9) + ";" + \
                       str(disk_quota * 0.9) + ";;;, minUptime=" + str(minUptime / SECONDS_TO_MINUTES) + ";10;10;;;," \
                                                                                           " maxUptime=" + str(
                maxUptime / SECONDS_TO_MINUTES) + ";10;10;;;, instances=" + str(runningInstances) + ";0;0;;;"

    nagiosState = 3  # Unknown
    if runningInstances == 0:
        nagiosState = 2  # Critical
        statusText = "Critical - no running instances"
    elif runningInstances < instances:
        nagiosState = 1  # Warning
        statusText = "Warning - not all instances are running"
    elif runningInstances == instances:
        if crashEventsLastMinutes:
            nagiosState = 0
            statusText = "Warning - crash events available"
        else:
            nagiosState = 0
            statusText = "OK"
    else:
        nagiosState = 3  # Unknown
        statusText = "Unknown - Unknown state"
    print (statusText + perfData)
    sys.exit(nagiosState)


def main(args):
    if (args.action == "appstats"):
        getAppStats(args)


if __name__ == "__main__":
    main(_args)  # TODO EIgenes Modul auslagern
