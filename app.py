from functools import wraps
from twisted.internet.defer import Deferred, DeferredList, inlineCallbacks, maybeDeferred, returnValue
from twisted.python import log
from twisted.web import client
from twisted.web.static import File
from twisted.web.template import Element, XMLFile, renderer

import HTMLParser, datetime, json, klein, re, treq

ALIASES = {
  "twitch": "Twitch Staff"
}

# Decrease noise in log files
client._HTTP11ClientFactory.noisy = False

def branchDeferred(deferred):
  branch = Deferred()
  def success(result):
    branch.callback(result)
    return result
  def failure(fail):
    branch.errback(fail)
    return fail
  deferred.addCallbacks(success, failure)
  return branch

@inlineCallbacks
def resolveClan(id):
  response = yield treq.get("http://www.bungie.net/Platform/Group/{!s}/".format(id))
  data = yield treq.json_content(response)
  clan = data["Response"]["detail"]

  if clan:
    if clan["memberCount"] > 1000:
      returnValue({
        "id": 0,
        "name": '"{}" is too big'.format(clan["name"]),
        "motto": "Clans with over 1,000 members can't be processed",
      })
    else:
      returnValue({
        "id": int(clan["groupId"]),
        "name": clan["name"],
        "motto": HTMLParser.HTMLParser().unescape(clan["about"]),
      })
  else:
    returnValue({
      "id": 0,
      "name": "No Clan Found",
      "motto": "Better luck next time"
    })

@inlineCallbacks
def lookupClan(name):
  response = yield treq.post("http://www.bungie.net/Platform/Group/Search/", data=json.dumps({
    "contents": {
      "searchValue": name
    },
    "currentPage": 1,
    "itemsPerPage": 1
  }))
  data = yield treq.json_content(response)

  if data["Response"]["results"]:
    clan = data["Response"]["results"][0]["detail"]
    if clan["memberCount"] > 1000:
      returnValue({
        "id": 0,
        "name": '"{}" is too big'.format(clan["name"]),
        "motto": "Clans with over 1,000 members can't be processed",
      })
    else:
      returnValue({
        "id": int(clan["groupId"]),
        "name": clan["name"],
        "motto": HTMLParser.HTMLParser().unescape(clan["about"]),
      })
  else:
    returnValue({
      "id": 0,
      "name": "No Clan Found",
      "motto": "Better luck next time"
    })

@inlineCallbacks
def lookupMembers(id):
  page = 1
  hasMore = True if id else False
  members = []
  characters = {
    "playstation": [],
    "xbox": []
  }
  deferreds = []

  while hasMore:
    # No idea what is different between V1, V2, and V3...
    response = yield treq.get("http://www.bungie.net/Platform/Group/{!s}/MembersV3/".format(id), params={
      "itemsPerPage": 50,
      "currentPage": page
    })
    data = yield treq.json_content(response)
    page += 1
    hasMore = data["Response"]["hasMore"]
    members.extend(data["Response"]["results"])

  # Load character data in parallel
  for member in members:
    deferreds.append(lookupCharacters(member, characters))
  yield DeferredList(deferreds, fireOnOneErrback=True, consumeErrors=True)

  for platform_characters in characters.values():
    platform_characters.sort(key=lambda c: (c["level"], c["light"]), reverse=True)

  returnValue(characters)

@inlineCallbacks
def lookupCharacters(member, characters):
  response = yield treq.get("http://www.bungie.net/Platform/User/GetBungieAccount/{!s}/254/".format(member["membershipId"]))
  data = yield treq.json_content(response)
  for account in data["Response"]["destinyAccounts"]:
    if account["userInfo"]["membershipType"] == 1:
      platform = "xbox"
    elif account["userInfo"]["membershipType"] == 2:
      platform = "playstation"
    else:
      continue

    # Load extra data about the characters, if we can
    extra_char_data = {}
    try:
      response = yield treq.get("http://www.bungie.net/Platform/Destiny/{!s}/Account/{!s}/".format(account["userInfo"]["membershipType"], account["userInfo"]["membershipId"]))
      extra_data = yield treq.json_content(response)
      extra_data = extra_data["Response"]["data"]["characters"]
      for character in extra_data:
        extra_char_data[character["characterBase"]["characterId"]] = character["characterBase"]
    except:
      pass

    for character in account["characters"]:
      character_data = {
        "bungieId": member["membershipId"],
        "accountId": account["userInfo"]["membershipId"],
        "characterId": character["characterId"],
        "name": account["userInfo"]["displayName"],
        "race": character["race"]["raceName"],
        "gender": character["gender"]["genderName"],
        "class": character["characterClass"]["className"],
        "level": character["level"],
        "levelString": "{:,d}".format(character["level"]),
        "icon": "http://bungie.net" + character["emblemPath"],
        "background": "http://bungie.net" + character["backgroundPath"],
        "profileUrl": "http://www.bungie.net/en/Legend/{!s}/{!s}/{!s}".format(account["userInfo"]["membershipType"], account["userInfo"]["membershipId"], character["characterId"]),

        # Default values for extra data (in case it fails)
        "light": 0,
        "lightString": "{:,d}".format(0),
        "grimoire": 0,
        "grimoireString": "{:,d}".format(0),
        "minutesPlayed": 0,
        "minutesPlayedString": "{:d}:{:02d}".format(0, 0),
        "lastSeen": "",
        "lastSeenString": ""
      }
      character_data["style"] = 'background: url("' + character_data["background"] + '")'

      if character["characterId"] in extra_char_data:
        extra_data = extra_char_data[character["characterId"]]
        try:
          character_data["light"] = extra_data["stats"]["STAT_LIGHT"]["value"] if "STAT_LIGHT" in extra_data["stats"] else 0
          character_data["lightString"] = "{:,d}".format(character_data["light"])
          character_data["grimoire"] = extra_data["grimoireScore"]
          character_data["grimoireString"] = "{:,d}".format(character_data["grimoire"])
          character_data["minutesPlayed"] = int(extra_data["minutesPlayedTotal"])
          character_data["minutesPlayedString"] = "{:d}:{:02d}".format(character_data["minutesPlayed"] / 60, character_data["minutesPlayed"] % 60)
          character_data["lastSeen"] = extra_data["dateLastPlayed"]
          character_data["lastSeenString"] = datetime.datetime.strptime(extra_data["dateLastPlayed"], "%Y-%m-%dT%H:%M:%SZ").strftime("%B %d, %I:%M%p")
        except:
          log.msg(repr(extra_data))

      characters[platform].append(character_data)

class ClanPage(Element):
  loader = XMLFile("clan.html")

  def __init__(self, clan):
    self._clan = clan
    self._members = branchDeferred(self._clan).addCallback(lambda c: c["id"]).addCallback(lookupMembers)

  def render(self, request):
    request.write("<!doctype html>\n")
    return Element.render(self, request)

  @renderer
  def title(self, request, tag):
    def render(clan):
      return tag(clan["name"] + " - Destiny Clan Roster")
    
    return branchDeferred(self._clan).addCallback(render)

  @renderer
  def header(self, request, tag):
    def render(clan):
      return tag(clan["name"])

    return branchDeferred(self._clan).addCallback(render)

  @renderer
  def subheader(self, request, tag):
    def render(clan):
      return tag(clan["motto"])

    return branchDeferred(self._clan).addCallback(render)

  @renderer
  def playstation(self, request, tag):
    def render(characters):
      for character in characters["playstation"]:
        yield tag.clone().fillSlots(**character)

    return branchDeferred(self._members).addCallback(render)

  @renderer
  def xbox(self, request, tag):
    def render(characters):
      for character in characters["xbox"]:
        yield tag.clone().fillSlots(**character)

    return branchDeferred(self._members).addCallback(render)

@klein.route('/<int:id>')
def clan_id(request, id):
  return ClanPage(resolveClan(id))

@klein.route('/<string:name>')
def clan_name(request, name):
  if name.lower() in ALIASES:
    name = ALIASES[name.lower()]

  return ClanPage(lookupClan(name))

@klein.route('/favicon.ico')
def favicon(request):
  return None

@klein.route('/robots.txt')
def favicon(request):
  return None

@klein.route('/', branch=True)
def index(request):
  with open("index.html", "r") as f:
    return f.read()

def monkeypatch_klein_render(render):
  @wraps(render)
  def new_render(request):
    host = request.getRequestHostname()
    port = getattr(request.getHost(), "port", 80)
    secure = request.isSecure()
    request.setHost(host, port, secure)
    return render(request)
  return new_render

def resource():
  klein_resource = klein.resource()
  klein_resource.render = monkeypatch_klein_render(klein_resource.render)
  return klein_resource
