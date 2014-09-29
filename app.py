from functools import wraps
from twisted.internet.defer import Deferred, DeferredList, inlineCallbacks, maybeDeferred, returnValue
from twisted.web.static import File
from twisted.web.template import Element, XMLFile, renderer

import datetime, json, klein, re, treq

ALIASES = {
  "twitch": "Twitch Staff"
}

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
def lookupClan(name):
  response = yield treq.post("http://www.bungie.net/Platform/Group/Search/", data=json.dumps({
    "contents": {
      "searchValue": name
    },
    "currentPage": 1,
    "itemsPerPage": 1
  }))
  data = yield treq.json_content(response)
  returnValue(int(data["Response"]["results"][0]["detail"]["groupId"]))

@inlineCallbacks
def lookupMembers(id):
  page = 1
  hasMore = True
  members = []
  characters = {
    "playstation": [],
    "xbox": []
  }
  deferreds = []

  while hasMore:
    # No idea what is different between V1, V2, and V3...
    response = yield treq.get("http://www.bungie.net/Platform/Group/" + str(id) + "/MembersV3/", params={
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
  response = yield treq.get("http://www.bungie.net/Platform/User/GetBungieAccount/" + member["membershipId"].encode("UTF-8") + "/254/")
  data = yield treq.json_content(response)
  for account in data["Response"]["destinyAccounts"]:
    if account["userInfo"]["membershipType"] == 1:
      platform = "xbox"
    elif account["userInfo"]["membershipType"] == 2:
      platform = "playstation"
    else:
      continue

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

        # Default values for extra data (in case it fails)
        "light": 0,
        "lightString": "{:,d}".format(0),
        "grimoire": 0,
        "grimoireString": "{:,d}".format(0),
        "minutesPlayed": 0,
        "minutesPlayedString": "{:,d}".format(0),
        "lastSeen": "",
        "lastSeenString": ""
      }
      character_data["style"] = 'background: url("' + character_data["background"] + '")'

      try:
        response = yield treq.get("http://www.bungie.net/Platform/Destiny/{!s}/Account/{!s}/Character/{!s}/".format(account["userInfo"]["membershipType"], account["userInfo"]["membershipId"], character["characterId"]))
        extra_data = yield treq.json_content(response)
        extra_data = extra_data["Response"]["data"]["characterBase"]
        character_data["light"] = extra_data["stats"]["STAT_LIGHT"]["value"]
        character_data["lightString"] = "{:,d}".format(character_data["light"])
        character_data["grimoire"] = extra_data["grimoireScore"]
        character_data["grimoireString"] = "{:,d}".format(character_data["grimoire"])
        character_data["minutesPlayed"] = int(extra_data["minutesPlayedTotal"])
        character_data["minutesPlayedString"] = "{:,d}".format(character_data["minutesPlayed"])
        character_data["lastSeen"] = extra_data["dateLastPlayed"]
        character_data["lastSeenString"] = datetime.datetime.strptime(extra_data["dateLastPlayed"], "%Y-%m-%dT%H:%M:%SZ").strftime("%B %d, %I:%M%p")
      except:
        pass

      characters[platform].append(character_data)

class ClanPage(Element):
  loader = XMLFile("clan.html")

  def __init__(self, clan_id, clan_name):
    self._clan_name = str(clan_name)
    self._clan_id = maybeDeferred(lambda: clan_id)
    self._members = self._clan_id.addCallback(lookupMembers)

  @renderer
  def title(self, request, tag):
    return tag(self._clan_name + " - Destiny Clan Roster")

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
  return ClanPage(id, id)

@klein.route('/<string:name>')
def clan_name(request, name):
  if not name: # Hack because route('/') didn't work :(
    return File("index.html")
  elif name == "favicon.ico":
    return None
  else:
    if name.lower() in ALIASES:
      name = ALIASES[name.lower()]

    return ClanPage(lookupClan(name), name)

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
