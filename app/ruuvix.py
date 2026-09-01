#!/usr/bin/env python3
import sys
import os
import argparse
from influxdb import InfluxDBClient
import json
import asyncio
import re
import logging
import grp
from logging.handlers import RotatingFileHandler
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from ruuvitag_sensor.ruuvi import RuuviTagSensor, RunFlag
from ruuvitag_sensor.ruuvitag import RuuviTag
import time
import signal
import random
from math import ceil
from statistics import mean, median

local = ZoneInfo("Europe/Helsinki")
logger = logging.getLogger("ruuvix")
name_re = re.compile(r"(?P<mac>\w{12})=(?P<name>\w+)")
run_flag = RunFlag()
# to make this script work with other scipts using bluetooth
lock_file = "/tmp/bt_resource.lock"
history = {}
tags = {}
influx_client = None
trigger_time = None
bt_in_progress_re = re.compile(r"org\.bluez\.Error\.InProgress|Operation already in progress", re.IGNORECASE)


def set_logging(debug):
  if debug or conf["debug"]:
    loglevel = logging.DEBUG
  else:
    loglevel = logging.INFO
  logger.setLevel(loglevel)

  path = os.path.dirname(os.path.realpath(sys.argv[0]))
  log_filename = os.path.join(path,f"ruuvix.log")
  max_log_size = 1024 * 1024  # 1 MB
  backup_count = 1
  file_handler = RotatingFileHandler(log_filename, mode="a", maxBytes=max_log_size, backupCount=backup_count)
  
  # Create a formatter
  formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  file_handler.setFormatter(formatter)

  # Create a handler to log to stdout (console)
  stdout_handler = logging.StreamHandler(sys.stdout)  
  stdout_handler.setLevel(loglevel)
  stdout_handler.setFormatter(formatter)

  # Add both handlers to the logger
  logger.addHandler(file_handler)
  logger.addHandler(stdout_handler)
  logger.info("Logging set")

def initialize():
  global history
  global influx_client

  signal.signal(signal.SIGTERM, sigterm_handler)
  signal.signal(signal.SIGINT, sigterm_handler)
  
  host, port = conf["influxdb"].split(":")
  influx_client = InfluxDBClient(host, port=int(port))
  influx_client.switch_database("ruuvix")
  logger.info("Initialized")

def read_config():
  global conf
  path = os.path.dirname(os.path.realpath(sys.argv[0]))
  os.chdir(path)
  
  with open("config.json", "r") as f:
    conf = json.loads(f.read())
  
  with open("ruuvi-names.properties") as f:
    for line in f.readlines():
      mac_tag = name_re.match(line)
      if line.strip().startswith("#"):
        pass
      elif mac_tag != None:
        name = mac_tag.group("name")
        mac = mac_tag.group("mac").upper()
        mac = ":".join([mac[i:i+2] for i in range(0, len(mac), 2)])
        tags[mac] = name

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError ("Type %s not serializable" % type(obj))

def sigterm_handler(_signo, _stack_frame):
    # Raises SystemExit(0):
    logger.info(f"Got terminate signal {_signo}")
    loop.stop()


def format_exception():
  exc_type, exc_obj, exc_tb = sys.exc_info()
  # walk to the innermost frame, i.e. where the error actually occurred
  while exc_tb.tb_next is not None:
    exc_tb = exc_tb.tb_next
  fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
  return exc_type, str(exc_obj), fname, exc_tb.tb_lineno


def is_bt_in_progress_error(exc):
  return bt_in_progress_re.search(str(exc)) is not None


async def close_generator(gen):
  if gen is None:
    return

  try:
    await gen.aclose()
  except Exception as close_error:
    logger.debug(f"Generator close failed: {close_error}")

def calibrate(calibrate_list):
  global trigger_time
  data_points = []
  reserve_bt()
  trigger_time = datetime.now(timezone.utc) + timedelta(seconds=30)
  try:
    loop.run_until_complete(get_data())
    # scan for 15 seconds
    found = []
    for mac, measurement in history.items():
        last_seen = measurement["data"]["fields"]["last_seen"]
        name = measurement["data"]["tags"]["name"]
        if name in calibrate_list:
          found.append(name)
          if last_seen < 30:
            data_points.append(measurement["data"])
          else: 
            logger.warning(f"No new data from {name}, cannot calibrate")
  except:
    logger.error(format_exception())
  finally:
    release_bt()

  for name in calibrate_list:
    if name not in found:
      logger.error(f"Did not find {name}, cannot calibrate it!")
      return

  measurements = {"names": [],"humidity":[],"temperature":[],"pressure":[]}
  for tag in data_points:
    name = tag["tags"]["name"]
    humidity = tag["fields"]["humidity"] if "humidity" in tag["fields"].keys() else None
    temperature = tag["fields"]["temperature"] if "temperature" in tag["fields"].keys() else None
    pressure = tag["fields"]["pressure"] if "pressure" in tag["fields"].keys() else None

    logger.info(f"Values for {name}: H {humidity} T {temperature} P {pressure}")
    measurements["names"].append(name)
    measurements["humidity"].append(humidity)
    measurements["temperature"].append(temperature)
    measurements["pressure"].append(pressure)

  averages = {}
  if len(calibrate_list) > 2:
    for key, values in measurements.items():
      if key != "names":
        filtered = avg_with_discard(measurements["names"].copy(), key, values.copy())
        averages[key] = mean(filtered)
  
    logger.debug(f"Averages: {json.dumps(averages)}")
  else:
    match_tag = calibrate_list[1]
    target_tag = calibrate_list[0]

    logger.info(f"Matching {target_tag} to {match_tag}")
    for tag in data_points:
      name = tag["tags"]["name"]
      if name == match_tag:
        averages = {
          "humidity": tag["fields"]["humidity"],
          "temperature":  tag["fields"]["temperature"],
          "pressure": tag["fields"]["pressure"]
        }
    if len(averages) < 2:
      logger.error("Could not match since values were not found for both tags!")
      return

  corrections = {}
  if os.path.isfile("ruuvi-corrections.json"):
    with open("ruuvi-corrections.json", "r") as f:
      corrections = json.loads(f.read())
  
  for tag in data_points:
    name = tag["tags"]["name"]    
    humidity = tag["fields"]["humidity"]
    temperature = tag["fields"]["temperature"]
    pressure = tag["fields"]["pressure"]
    
    if name not in corrections.keys():
      corrections[name] = {}

    if humidity != None:
      corrections[name]["humidity"] = averages["humidity"] - humidity
    
    if temperature != None:
      corrections[name]["temperature"] = averages["temperature"] - temperature
    
    if pressure != None:
      corrections[name]["pressure"] = averages["pressure"] - pressure

  logger.info(f"Calibrations: {corrections}")

  with open("ruuvi-corrections.json", "w") as f:
    f.write(json.dumps(corrections))
  
def avg_with_discard(names, type, values):
  # remove none values
  filtered_values = []
  filtered_names = []
  for i in range(len(values)):
    if values[i] != None:
      filtered_values.append(values[i])
      filtered_names.append(names[i])
  
  values = filtered_values
  names = filtered_names      
  
  m = median(values)
  maxdev = 0
  
  d = None
  for i in range(len(values)):
    v = values[i]
    dev = abs(v - m)
    if abs(v - m) > maxdev:
      d = i
      maxdev = dev
  popped_name = names.pop(d)
  popped_value = values.pop(d)
  logger.info(f"Ignored {popped_name} {type} {popped_value}: deviation {maxdev}")
  return values

async def ask_exit(signame):
    logger.info("Got async signal %s: exit" % signame)
    loop.stop()
    sys.exit(0)

async def get_data():
  start = datetime.now(timezone.utc)
  logger.debug("Start async data")
  for attempt in range(1, 4):
    gen = None
    try:
      gen = RuuviTagSensor.get_data_async()
      async for found_data in gen:
        mac = found_data[0]
        if mac not in tags:
          logger.debug(f"Ignoring unknown tag {mac}")
          continue

        name = tags[mac]
        now = datetime.now(timezone.utc)
        if mac in history.keys():
          seen = history[mac]["seen"]
          since = now - seen
        else:
          since = timedelta(seconds=0)
        extra_data = {"last_seen": since.total_seconds()}
        data = {**found_data[1], **extra_data}
        data.pop("mac") # remove the mac from measurements since it is tag value

        data_point = {
          "measurement": "ruuvi_measurements",
          "tags": {
              "mac": mac,
              "name": name,
              "client":  os.uname()[1]
          },
          "time": now.isoformat(),
          "fields": data
        }
        history[mac] = {"data":data_point, "seen": now}
        logger.debug(f"Measurements from {name}")

        # stop the run once we are near even minute or past the start
        # TODO: handle this with scan_rate instead
        if now - start > timedelta(seconds=30) or trigger_time != None and now > trigger_time:
          logger.debug(f"Stopping async data: {start} < {now} <> {trigger_time}")
          return

      logger.debug("Stopped async data")
      return
    except Exception as e:
      if is_bt_in_progress_error(e) and attempt < 3:
        delay = attempt * 5
        logger.warning(f"Bluetooth scan already in progress, retrying in {delay} seconds (attempt {attempt}/3)")
        await asyncio.sleep(delay)
      else:
        logger.error(f"Error occured: {e}")
        return
    finally:
      await close_generator(gen)

  logger.error("Bluetooth scan did not recover after retries")

def scan():
  data_points = []
  reserve_bt()
  try:
    loop.run_until_complete(get_data())

    scan_rate = conf["scan_rate"]
    
    # sleep random
    time.sleep(random.random())
    
    latest = query_latest()
    
    for mac, measurement in history.items():
      last_seen = measurement["data"]["fields"]["last_seen"]
      name = measurement["data"]["tags"]["name"] 

      if name in latest:
        logger.info(f"{name} already updated")
      elif last_seen > scan_rate:
        logger.warning(f"No new data from {name} during scan! Last seen {last_seen} seconds ago.")
      else:
        data_points.append(run_corrections(name, measurement["data"]))

    # Write the data point to the database
    if len(data_points) > 0:
      names = ", ".join([i["tags"]["name"] for i in data_points])
      logger.info(f"Got {len(data_points)} measurements during scan: {names}")
      #logger.debug(json.dumps(data_points))
    else:
      logger.info("Got 0 data points during scan")
  except:
    logger.error(format_exception())
  finally:
    release_bt()

  return data_points

def run_corrections(tag_name, data):
  with open("ruuvi-corrections.json", "r") as f:
    corrections = json.loads(f.read())
  
  if tag_name in corrections.keys():
    for key, value in data["fields"].items():
      if key in corrections[tag_name].keys():
        data["fields"][key] = value + corrections[tag_name][key]
    return data
  else:
    return data

def init():
  global conf
  retention = conf["retention"]

  logger.info("Initializing ruuvix database...")
  if "ruuvix" in influx_client.get_list_database():
    influx_client.drop_database("ruuvix")
  influx_client.create_database("ruuvix")
  influx_client.switch_database("ruuvix")
  retention_policy = {
    "name": "ruuvix_retention_policy",
    "duration": retention,
    "replication": 1,
    "default": True
  }
  influx_client.create_retention_policy(**retention_policy)

async def find():
  logger.debug("Start async find")
  found = []
  unknown = []
  gen = None
  try:
    gen = RuuviTagSensor.get_data_async()
    async for found_data in gen:
      mac = found_data[0]
      if mac not in found and mac in tags.keys():
        name = tags[mac]
        logger.info(f"Found {name} {mac}")
        found.append(mac)
      elif mac not in unknown and mac not in tags.keys():
        logger.info(f"Unknown Ruuvitag {mac}")
        unknown.append(mac)

  except KeyboardInterrupt:
    logger.debug(f"Stopping async find")

  except Exception as e:
    logger.error(f"Error occured: {e}")
  finally:
    await close_generator(gen)
  logger.debug("Stopped async find")

def listen(listen):
  if listen != "all":
    target_name = None
    target_mac = None
    for mac, name in tags.items():
      if listen == name:
        target_name = name
        target_mac = mac
        break
      elif listen == mac:
        target_name = name
        target_mac = mac
        break
  
    logger.info(f"Listening to {target_name} {target_mac}")
    sensor = RuuviTag(mac)
    while True:  
      try:
        state = sensor.update()
        logger.info(name, state)
      except Exception as e:
        logger.error(f"Error occured: {e}")
        break
  else:
    while True:
      for mac, name in tags.items():
        logger.info(f"Listening to {name} {mac}")
        sensor = RuuviTag(mac)
        state = sensor.update()
        logger.info(name, state)

async def listen_async(listen):
  target_name = None
  target_mac = None
  for mac, name in tags.items():
    if listen == name:
      target_name = name
      target_mac = mac
      break
    elif listen == mac:
      target_name = name
      target_mac = mac
      break
  count = 0
  logger.info(f"Listening to {target_name} {target_mac}")
  try:
    async for found_data in RuuviTagSensor.get_data_async():
      mac, data = found_data
      if mac == target_mac:
        count += 1
        print(f"Data from {tags[mac]} ({mac}): {data}")
      await asyncio.sleep(0.1)
      if count >= 5:
        break
  except asyncio.CancelledError:
      print("Listener cancelled.")
  except KeyboardInterrupt:
      print("Listener stopped by user.")


def wait_for_time():
  """ wait for even time before starting scan """
  global trigger_time

  logger.debug(f"Last run: {trigger_time}")
  scan_rate = conf["scan_rate"]
  now = datetime.now(timezone.utc)

  # default last run to last even minute
  # TODO: handle case for scan_rate larger than 60 seconds
  if trigger_time == None:
    trigger_time = now.replace(second=0, microsecond=0)
  # schedule scanning 31 secs before the measurement time
  target_time = trigger_time + timedelta(seconds=scan_rate - 31)
  logger.debug(f"Schedule: {trigger_time} < {now} < {target_time}: {trigger_time < now < target_time}")
  time_difference = target_time - now
  seconds_until_next_event = ceil(max(time_difference.total_seconds(), 0))

  logger.info(f"Sleeping for {seconds_until_next_event} seconds before next measurement at {target_time.astimezone(local).isoformat()}")
  time.sleep(seconds_until_next_event)
  logger.info(f"Sleep end")

  trigger_time = trigger_time + timedelta(seconds=scan_rate)
  logger.debug(f"Next trigger time: {trigger_time}")
  

def reserve_bt():
  try:
    while os.path.exists(lock_file):
      mtime = os.path.getmtime(lock_file)
      nowtime = datetime.now(timezone.utc).timestamp()
      # wait maximum of two minutes
      if (nowtime - mtime) > 120:
        logger.error("Removing stale lock file...")
        os.remove(lock_file)
      else:
        logger.info("Ruuvix waiting for bt")
        time.sleep(1)

    with open(lock_file, "w") as f:
      f.write("Ruuvix using BT")
    os.chmod(lock_file, 0o666)  # Read and write for all
  except KeyboardInterrupt:
    logger.info("Interrupted while waiting for bt")
    raise


def release_bt():
  if os.path.exists(lock_file):
    os.remove(lock_file)

def query_latest():
  global influx_client
  global conf
  end_time = datetime.now(timezone.utc)
  start_time = end_time - timedelta(minutes=1)
  query_str = f"SELECT * FROM ruuvi_measurements WHERE time >= '{start_time.isoformat()}' AND time <= '{end_time.isoformat()}'"
  logger.debug(query_str)

  result = influx_client.query(query_str)
  points = list(result.get_points())
  tags = []
  for measurement in points:
    logger.debug(f"Last value for {measurement['name']}: {measurement['time']}")
    tags.append(measurement["name"])
  return tags
  
def run():
  try:
    logger.info("Starting run loop")
    while True:
      try:
        wait_for_time()
        logger.info("Starting scan")
        data_points = scan()
        write_data(data_points)
      except Exception as e:
        logger.error(format_exception())
  finally:
    logger.info("Quitting")

def write_data(data_points):
  global influx_client
  if len(data_points) > 0:
    logger.info("Writing to database...")
    #logger.debug(json.dumps(data_points, default=json_serial))
    influx_client.switch_database("ruuvix")
    influx_client.write_points(data_points)
    logger.info("Write to db finished")
    logger.debug(f"Wrote {len(data_points)} data points")
  else:
    logger.info("No new data to write")

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Ruuvix, ruuvi tag server")
  parser.add_argument("--listen", help="Try listen to known tag by name or mac")
  parser.add_argument("--find", action="store_true", help="Try to find visible tags")
  parser.add_argument("--scan", action="store_true", help="Try to get configured tags for one minute")
  parser.add_argument("--run", action="store_true", help="Start monitoring tags and write to db")
  parser.add_argument("--query", action="store_true", help="Query latest datas from influx")
  parser.add_argument("--init", action="store_true", help="Init the influx db")
  parser.add_argument("--calibrate", nargs="+", help="Calibrate the chosen tags to their average values (should be close to each other)")
  parser.add_argument("--debug", action="store_true", help="Debug logging")
  
  args = parser.parse_args()
  loop = None
  try:
    read_config()
    set_logging(args.debug)  
    initialize()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda signame=signame: asyncio.create_task(ask_exit(signame))
        )

    if args.find:
      loop.run_until_complete(find())
    elif args.calibrate:
      calibrate(args.calibrate)
    elif args.scan:
      scan()
    elif args.listen != None:
      #listen(args.listen)
      asyncio.run(listen_async(args.listen))
    elif args.run:
      run()
    elif args.init:
      init()
    elif args.query:
      query_latest()
    else:
      logger.info(conf)
  except KeyboardInterrupt:
    print("Interrupted by user, exiting cleanly.")
  except Exception as e:
    logger.error(format_exception())
  finally:
    if loop is not None and not loop.is_closed():
      loop.close()
    release_bt()
