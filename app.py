from klein import resource, route
from twisted.internet.defer import inlineCallbacks, maybeDeferred, returnValue
from twisted.web.static import File
from twisted.web.template import Element, XMLFile

import treq, json, re

ALIASES = {
  "twitch": "Twitch Staff"
}

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
    response = yield treq.get("http://www.bungie.net/platform/Group/" + str(id) + "/MembersV3/", params={
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
  yield deferreds

  for platform_characters in characters.values():
    platform_characters.sort(key=lambda c: (c["level"], c["light"]), reverse=True)

  returnValue(characters)

@inlineCallbacks
def lookupCharacters(member, characters):
  response = yield treq.get("http://www.bungie.net/platform/User/GetBungieAccount/" + member["membershipId"] + "/254/")
  data = yield treq.json_content(response)
  for account in data["Response"]["destinyAccounts"]:
    if account["userInfo"]["membershipType"] == 1:
      platform = "xbox"
    elif account["userInfo"]["membershipType"] == 2
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
        "light": 0,
        "light": 0,
        "icon": "http://bungie.net" + character["emblemPath"],
        "background": "http://bungie.net" + character["backgroundPath"]
      }
      character_data["style"] = 'background: url("' + character_data["background"] + '")'
      
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
      for character in members["playstation"]:
        yield tag.clone().fillSlots(**character)

    return self._members.addCallback(render)

  @renderer
  def xbox(self, request, tag):
    def render(characters):
      for character in members["xbox"]:
        yield tag.clone().fillSlots(**character)

    return self._members.addCallback(render)

@route('/')
def index(request):
  return File("index.html")

@route('/<int:id>')
def clan_id(request):
  id = request
  return ClanPage(id, id)

@route('/<string:name')
def clan_name(request):
  name = request

  if name.lower() in ALIASES:
    name = ALIASES[name.lower()]

  return ClanPage(lookupClan(name), name)
