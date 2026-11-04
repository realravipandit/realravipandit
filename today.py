"""Refresh the GitHub stats shown in dark_mode.svg / light_mode.svg.

Environment variables:
  ACCESS_TOKEN  a personal access token (classic) with `repo` and `read:user` scopes
  USER_NAME     your GitHub username
"""
import datetime
import os
import re
from pathlib import Path

import requests

API = "https://api.github.com/graphql"
SVG_FILES = ["dark_mode.svg", "light_mode.svg"]
WIDTH = 56  # must match the info-column width used when the SVGs were built


def gql(query, variables, token):
    r = requests.post(
        API,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {token}"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def user_basics(user, token):
    q = """query($login:String!){ user(login:$login){
        id createdAt
        followers{totalCount}
        repositories(ownerAffiliations:OWNER){totalCount}
        repositoriesContributedTo(contributionTypes:[COMMIT,PULL_REQUEST,REPOSITORY]){totalCount}
    }}"""
    return gql(q, {"login": user}, token)["user"]


def total_stars(user, token):
    q = """query($login:String!,$after:String){ user(login:$login){
        repositories(first:100,after:$after,ownerAffiliations:OWNER){
            pageInfo{hasNextPage endCursor} nodes{stargazerCount}}}}"""
    stars, after = 0, None
    while True:
        repos = gql(q, {"login": user, "after": after}, token)["user"]["repositories"]
        stars += sum(n["stargazerCount"] for n in repos["nodes"])
        if not repos["pageInfo"]["hasNextPage"]:
            return stars
        after = repos["pageInfo"]["endCursor"]


def total_commits(user, created_at, token):
    q = """query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){
        contributionsCollection(from:$from,to:$to){totalCommitContributions
        restrictedContributionsCount}}}"""
    start = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    total = 0
    while start < now:
        end = min(start.replace(year=start.year + 1), now)
        c = gql(q, {"login": user, "from": start.isoformat(), "to": end.isoformat()}, token)
        c = c["user"]["contributionsCollection"]
        total += c["totalCommitContributions"] + c["restrictedContributionsCount"]
        start = end
    return total


def lines_of_code(user, user_id, token):
    """Additions/deletions in commits authored by you, across repos you own or collaborate on."""
    repos_q = """query($login:String!,$after:String){ user(login:$login){
        repositories(first:50,after:$after,
          ownerAffiliations:[OWNER,COLLABORATOR,ORGANIZATION_MEMBER]){
          pageInfo{hasNextPage endCursor} nodes{nameWithOwner}}}}"""
    hist_q = """query($owner:String!,$name:String!,$id:ID!,$after:String){
        repository(owner:$owner,name:$name){ defaultBranchRef{ target{ ... on Commit{
          history(first:100,after:$after,author:{id:$id}){
            pageInfo{hasNextPage endCursor} nodes{additions deletions}}}}}}}"""
    names, after = [], None
    while True:
        repos = gql(repos_q, {"login": user, "after": after}, token)["user"]["repositories"]
        names += [n["nameWithOwner"] for n in repos["nodes"]]
        if not repos["pageInfo"]["hasNextPage"]:
            break
        after = repos["pageInfo"]["endCursor"]

    add = dele = 0
    for full in names:
        owner, name = full.split("/")
        after = None
        while True:
            ref = gql(hist_q, {"owner": owner, "name": name, "id": user_id, "after": after}, token)
            ref = ref["repository"]["defaultBranchRef"]
            if not ref:  # empty repo
                break
            hist = ref["target"]["history"]
            for n in hist["nodes"]:
                add += n["additions"]
                dele += n["deletions"]
            if not hist["pageInfo"]["hasNextPage"]:
                break
            after = hist["pageInfo"]["endCursor"]
    return add, dele


def dots(key_len, value_len):
    return " " + "." * max(WIDTH - key_len - value_len - 2, 1) + " "


def set_text(svg, element_id, text):
    pattern = rf'(id="{re.escape(element_id)}"[^>]*>)[^<]*(<)'
    return re.sub(pattern, lambda m: m.group(1) + text + m.group(2), svg, count=1)


def update_svg(path, s):
    svg = Path(path).read_text(encoding="utf-8")
    simple = {
        "repo_data": ("Repositories", s["repos"]),
        "contrib_data": ("Contributed to", s["contrib"]),
        "star_data": ("Stars", s["stars"]),
        "commit_data": ("Commits", s["commits"]),
        "follower_data": ("Followers", s["followers"]),
    }
    for eid, (key, val) in simple.items():
        text = f"{val:,}"
        svg = set_text(svg, eid, text)
        svg = set_text(svg, eid.replace("_data", "_data_dots"), dots(len(key), len(text)))
    loc_total = s["loc_add"] - s["loc_del"]
    loc_text = f"{loc_total:,}"
    add_text, del_text = f"{s['loc_add']:,}", f"{s['loc_del']:,}"
    svg = set_text(svg, "loc_data", loc_text)
    svg = set_text(svg, "loc_add", add_text)
    svg = set_text(svg, "loc_del", del_text)
    visible = len(f"{loc_text} ( {add_text}++, {del_text}-- )")
    svg = set_text(svg, "loc_dots", dots(len("Lines of code"), visible))
    Path(path).write_text(svg, encoding="utf-8")


def main():
    token, user = os.environ["ACCESS_TOKEN"], os.environ["USER_NAME"]
    basics = user_basics(user, token)
    add, dele = lines_of_code(user, basics["id"], token)
    stats = {
        "repos": basics["repositories"]["totalCount"],
        "contrib": basics["repositoriesContributedTo"]["totalCount"],
        "followers": basics["followers"]["totalCount"],
        "stars": total_stars(user, token),
        "commits": total_commits(user, basics["createdAt"], token),
        "loc_add": add,
        "loc_del": dele,
    }
    for f in SVG_FILES:
        update_svg(f, stats)
    print("Updated:", stats)


if __name__ == "__main__":
    main()
