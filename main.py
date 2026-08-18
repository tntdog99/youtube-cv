from googleapiclient.discovery import build
from gettoken import get_youtube_token
from googleapiclient.http import BatchHttpRequest
from googleapiclient.errors import HttpError
from rich import print as rprint
from rich.console import Console
from rich.padding import Padding
from prompt_toolkit import PromptSession

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.keys import Keys

kb = KeyBindings()



import dill
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from rich.text import Text
import pathlib
import platform

creds = get_youtube_token()
youtube = build("youtube", "v3", credentials=creds)

CANCEL_TOKEN = "/cancel"
if pathlib.Path('./.config').exists() == False:
    print("making default config, timezone: UTC, terminal: plain")
    
    with open("./.config", "w") as f:
        config = {
            "_comment": """
            timezone uses the IANA time zone format,
            terminal:
            set to 'wt' (windows terminal) for auto scrolling back to the top, use plain for any other terminal
            
            """,
            "timezone": "UTC",
            "terminal": "plain",
            
            }
        
        json.dump(config, f)

with open(f"./.config", "r") as f:
    config = json.load(f)
comment_timezone = config.get('timezone', "UTC")
terminal = config.get('terminal', "plain")
OS = platform.system()




if terminal == "wt":
    import pyautogui
    
    

class Cancelled(Exception):

    pass


def prompt_choice(prompt_text, choices=None, default=None, case_sensitive=False):
    session = PromptSession()

    if choices:
        # make a copy and ensure cancel appears as an option
        choices_list = list(choices)
        if CANCEL_TOKEN not in [c.lower() for c in choices_list]:
            choices_list.append(CANCEL_TOKEN)

        label = f"{prompt_text} [{'/'.join(choices_list)}]" if prompt_text else f"[{'/'.join(choices_list)}]"
        if default is not None:
            label += f" ({default})"

        valid_lookup = choices_list if case_sensitive else [c.lower() for c in choices_list]

        while True:
            res = session.prompt(f"{label}: ")
            if not res and default is not None:
                res = default
            check = res if case_sensitive else res.lower()
            if check in valid_lookup:
                if check == CANCEL_TOKEN:
                    raise Cancelled()
                return res if case_sensitive else check
            print(f"Please choose one of: {', '.join(choices_list)}")
    else:
        res = session.prompt(f"{prompt_text}: ")
        if not res and default is not None:
            res = default
        if isinstance(res, str) and res.strip().lower() == CANCEL_TOKEN:
            raise Cancelled()
        return res



def prompt_text(prompt_text, default=None):
    session = PromptSession()
    result = session.prompt(f"{prompt_text}")
    if not result and default is not None:
        result = default
    if isinstance(result, str) and result.strip().lower() == CANCEL_TOKEN:
        raise Cancelled()
    return result


def session_prompt(session: PromptSession, prompt_text, default=None):
    """
    Wrapper for PromptSession.prompt (free text, supports multiline etc).
    Raises Cancelled on cancel token.
    """
    res = session.prompt(f"{prompt_text}")
    if isinstance(res, str) and res.strip().lower() == CANCEL_TOKEN:
        raise Cancelled()
    return res


def fetch_all_comments(video_id):

    all_comments = []

    request = youtube.commentThreads().list(
        part="snippet",
        videoId=video_id,
        maxResults=100
    )
    print("got first request")

    while request:
        response = request.execute()
        items = response.get("items", [])

        # Keep page-local structures so we can preserve order exactly as before
        page_entries = []  # list of (parent_id, comment_data, totalReplyCount)
        parents_with_replies = {}  # parent_id -> comment_data
        next_tokens = {}  # parent_id -> nextPageToken from first-page response (if any)

        for item in items:
            top = item["snippet"]["topLevelComment"]
            parent_id = top["id"]
            comment_data = top["snippet"]
            comment_data["id"] = top["id"]
            comment_data["replies"] = []
            total_replies = item["snippet"].get("totalReplyCount", 0)
            page_entries.append((parent_id, comment_data, total_replies))
            if total_replies > 0:
                parents_with_replies[parent_id] = comment_data

        if parents_with_replies:
            # Use the YouTube-specific batch endpoint and fall back to sequential on error.
            batch = BatchHttpRequest(batch_uri="https://www.googleapis.com/batch/youtube/v3")

            def make_callback(pid):
                def _callback(request_id, response, exception, pid=pid):
                    if exception:
                        # let the outer try/except handle fallback
                        raise exception
                    for reply in response.get("items", []):
                        reply_data = reply["snippet"]
                        reply_data["id"] = reply["id"]  # include the comment ID
                        parents_with_replies[pid]["replies"].append(reply_data)

                    tok = response.get("nextPageToken")
                    if tok:
                        next_tokens[pid] = tok
                return _callback

            for parent_id in list(parents_with_replies.keys()):
                req = youtube.comments().list(
                    part="snippet",
                    parentId=parent_id,
                    maxResults=100
                )
                batch.add(req, callback=make_callback(parent_id))

            try:
                batch.execute()
            except HttpError:
                # Batch endpoint not available or other HTTP batch error — fall back to sequential requests
                for parent_id in list(parents_with_replies.keys()):
                    replies_request = youtube.comments().list(
                        part="snippet",
                        parentId=parent_id,
                        maxResults=100
                    )
                    replies_response = replies_request.execute()
                    for reply in replies_response.get("items", []):
                        reply_data = reply["snippet"]
                        reply_data["id"] = reply["id"]
                        parents_with_replies[parent_id]["replies"].append(reply_data)

                    tok = replies_response.get("nextPageToken")
                    if tok:
                        next_tokens[parent_id] = tok

            # For any parent that had more than one page of replies, fetch remaining pages sequentially.
            for parent_id in list(parents_with_replies.keys()):
                token = next_tokens.get(parent_id)
                while token:
                    replies_request = youtube.comments().list(
                        part="snippet",
                        parentId=parent_id,
                        pageToken=token,
                        maxResults=100
                    )
                    replies_response = replies_request.execute()
                    for reply in replies_response.get("items", []):
                        reply_data = reply["snippet"]
                        reply_data["id"] = reply["id"]
                        parents_with_replies[parent_id]["replies"].append(reply_data)

                    token = replies_response.get("nextPageToken")

        # Append page comments to global list in the exact same order as original code,
        # printing the same status line before each append.
        for parent_id, comment_data, _ in page_entries:
            print(f"added new comment, total so far: {len(all_comments)} replies: {len(comment_data['replies'])}")
            all_comments.append(comment_data)

        request = youtube.commentThreads().list_next(request, response)

    return all_comments


def load_comments_from_file(video_id):
    with open(f"./videos/{video_id}.bin", "rb") as f:
        return dill.load(f)


def highlight(text, term):
    t = Text(text)
    start = 0
    while True:
        idx = t.plain.lower().find(term.lower(), start)
        if idx == -1:
            break
        t.stylize("black on white", idx, idx + len(term))
        start = idx + len(term)
    return t

def date_format(date):
    date_formatted = (datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(ZoneInfo(comment_timezone)))
    date_now = datetime.now(ZoneInfo(comment_timezone))
    
    
    delta = date_now - date_formatted
    mins = int(delta.total_seconds() / 60)
    hours = int(delta.total_seconds() / 3600)
    age = ''
    if mins < 1:
        age += f"{int(delta.total_seconds())} second(s) ago"
    
    elif mins <= 60:
        age += f"{mins} minute(s) ago"
    elif hours <= 24:
        
        age += f"{hours} hour(s) ago"
    elif delta.days <= 365:
        age += f'{delta.days} day(s) ago'
    else:
        years = delta.days // 365.25
        age += f'{years} year(s) ago'
    
    
    return f'{date_formatted.strftime("%m/%d/%Y %I:%M %p").lstrip("0").replace("/0", "/")}, {age}'

def print_comment(comment, filter_term=None, commentnumberhighlight=None):
    body = Text()

    author = comment["authorDisplayName"]
    if filter_term:
        author_display = highlight(author, filter_term)
    else:
        author_display = author
    body.append(author_display)
    body.append(f": likes: {comment['likeCount']}\n")

    posted = comment["publishedAt"]
    updated = comment["updatedAt"]
    if posted != updated:
        body.append(F"posted: {date_format(posted)}, updated: {date_format(updated)}\n")
    else:
        body.append(F"posted: {date_format(posted)}\n")
    body.append("--------------------\n")
    text = comment["textOriginal"]
    if filter_term:
        body.append(highlight(text, filter_term))
    else:
        body.append(text)

    body.append("\n--------------------")
    if commentnumberhighlight == 0:
        body.append("<<<")
    body.append("\n")
    body.append(f"replies: {len(comment['replies'])}\n")

    rprint(body)

    if comment["replies"]:
        for index, reply in enumerate(comment["replies"], start=1):
            reply_author = reply["authorDisplayName"]
            if filter_term:
                reply_author_display = highlight(reply_author, filter_term)
                reply_text = highlight(reply["textOriginal"], filter_term)

            else:
                reply_author_display = reply_author
                reply_text = Text(reply["textOriginal"])
            header = Text()

            footer = Text()
            header.append(reply_author_display)
            header.append(f": likes: {reply['likeCount']}\n")
            posted = reply["publishedAt"]
            updated = reply["updatedAt"]
            if posted != updated:
                header.append(F"posted: {date_format(posted)}, updated: {date_format(updated)}\n")
            else:
                header.append(F"posted: {date_format(posted)}\n")
            header.append("--------------------\n")
            reply_text_full = Padding(reply_text, (0, 0, 0, 4))
            footer.append("\n--------------------")
            if index == commentnumberhighlight:
                footer.append("<<<")
            footer.append("\n")
            rprint(header, reply_text_full, footer)


def comment_on_video(video_id, comment_text):
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {
                "snippet": {
                    "textOriginal": comment_text
                }
            }
        }
    }
    request = youtube.commentThreads().insert(
        part="snippet",
        body=body,
    )
    response = request.execute()


def reply_to_comment(parent_id, reply_text):
    body = {
        "snippet": {
            "parentId": parent_id,
            "textOriginal": reply_text
        }
    }
    request = youtube.comments().insert(
        part="snippet",
        body=body,
    )
    response = request.execute()


def edit_comment(comment_text, comment_id):
    body = {
        'id': comment_id,
        'snippet': {'textOriginal': comment_text}
    }
    request = youtube.comments().update(
        part='snippet',
        body=body
    )
    response = request.execute()


print("Fetching comments...")
video_id = "5XTHsdMPPQs"
comments = fetch_all_comments(video_id)
os.makedirs("./videos", exist_ok=True)

with open(f"./videos/{video_id}.bin", "wb") as f:
    dill.dump(comments, f)
os.system('cls')
rprint("Fetched comments")
filtered_comments = comments.copy()
commentn = 0
old = -1
sort_pars = ["none"]

def render():
    global old
    if old != commentn:
        os.system('cls')
        rprint(f"Comment {commentn + 1} of {len(filtered_comments)}")
        rprint(f"Sorted by: {', '.join(sort_pars)}")
        if len(filtered_comments) != 0:
            print_comment(filtered_comments[commentn], filter_term=search_term if 'search_term' in globals() else None)
        else:
            print("No comments")

        time.sleep(0.1)
        if terminal == 'wt':
            pyautogui.hotkey('ctrl', 'shift', 'home')
        old = commentn


def filtered_empty_block():

    if len(filtered_comments) == 0 and len(comments) != 0:
        os.system('cls')
        rprint("No comments match the filter criteria.")
        return True
    return False


def reply_nav_app():
    state = {"replyn": 0, "oldreply": -1}
    nav_kb = KeyBindings()

    def refresh_nav():
        if state["oldreply"] != state["replyn"]:
            os.system('cls')
            rprint(f"reply {state['replyn'] + 1} of {len(filtered_comments[commentn]['replies']) + 1}")
            rprint(f"Sorted by: {', '.join(sort_pars)}")
            
            if len(filtered_comments) != 0:
                print_comment(
                    filtered_comments[commentn],
                    filter_term=search_term if 'search_term' in globals() else None,
                    commentnumberhighlight=state["replyn"],
                )
            else:
                print("No comments")

            time.sleep(0.1)
            if terminal == 'wt':
                pyautogui.hotkey('ctrl', 'shift', 'home')
            state["oldreply"] = state["replyn"]

    refresh_nav()

    @nav_kb.add("w")
    def _(event):
        if state["replyn"] > 0:
            state["replyn"] += -1
        refresh_nav()

    @nav_kb.add("s")
    def _(event):
        if state["replyn"] < len(filtered_comments[commentn]['replies']):
            state["replyn"] += 1
        refresh_nav()

    @nav_kb.add("enter")
    def _(event):
        event.app.exit()

    nav_app = Application(
        key_bindings=nav_kb,
        layout=Layout(Window(FormattedTextControl(text=""), height=1)),
        full_screen=False,
    )
    nav_app.run()
    return state["replyn"]


def do_edit():
    global old
    replyn = reply_nav_app()
    session = PromptSession()
    try:
        editted_text = session_prompt(session, "edit: ")
    except Cancelled:
        os.system('cls')
        old = -1
        return
    editted_text = editted_text.encode('utf-8').decode('unicode_escape')
    os.system("cls")
    if replyn == 0:
        commentid = filtered_comments[commentn]['id']
    else:
        commentid = filtered_comments[commentn]['replies'][replyn - 1]['id']
    edit_comment(comment_text=editted_text, comment_id=commentid)
    old = -1


def do_delete():
    global old
    replyn = reply_nav_app()
    try:
        confirm = prompt_text("do you really want to delete this comment? enter 'CONFIRM' to confirm: ")
    except Cancelled:
        os.system('cls')
        old = -1
        return
    if confirm == "CONFIRM":
        os.system("cls")
        if replyn == 0:
            comment_id = filtered_comments[commentn]['id']
        else:
            comment_id = filtered_comments[commentn]['replies'][replyn - 1]['id']
        youtube.comments().delete(id=comment_id).execute()
        old = -1
    else:
        os.system("cls")
        print("canceled")
        time.sleep(3)
        old = -1


def post_actions():
    global old
    os.system("cls")
    console = Console()
    try:
        option = prompt_choice("", choices=["reply", "comment", "edit", "delete"], default="reply")
        if option == "reply":
            if len(filtered_comments) != 0:
                print_comment(filtered_comments[commentn], filter_term=search_term if 'search_term' in globals() else None)
            else:
                print("No comments")
            session = PromptSession()
            reply_text = session_prompt(session, "reply text: ")
            reply_text = reply_text.encode('utf-8').decode('unicode_escape')
            reply_to_comment(parent_id=filtered_comments[commentn]["id"], reply_text=reply_text)
            old = -1
        if option == "comment":
            session = PromptSession()
            comment_text = session_prompt(session, "comment text: ")
            comment_text = comment_text.encode('utf-8').decode('unicode_escape')
            comment_on_video(video_id=video_id, comment_text=comment_text)
            old = -1
        if option == "edit":
            do_edit()
        if option == "delete":
            do_delete()
    except Cancelled:
        os.system('cls')
        old = -1

def change_video():
    global comments, filtered_comments, video_id, commentn, old, sort_pars
    os.system('cls')
    try:
        video_id_candidate = prompt_text("enter video id: ")
        load_from_file_or_fetch = prompt_choice("load from file or refrest?: ", choices=["file", "refresh"], default="file")
        if load_from_file_or_fetch == "file":
            comments = load_comments_from_file(video_id_candidate)
            video_id = video_id_candidate
        else:
            print("Fetching comments...")
            comments = fetch_all_comments(video_id_candidate)
            with open(f"./videos/{video_id_candidate}.bin", "wb") as f:
                dill.dump(comments, f)
            video_id = video_id_candidate
        filtered_comments = comments.copy()
        commentn = 0
        old = -1
        sort_pars = ["none"]
        os.system('cls')
        print("Fetched comments")
    except Cancelled:
        os.system('cls')
        old = -1



def filter_comments():
    global filtered_comments, commentn, old, search_term
    os.system('cls')
    console = Console()
    try:
        include_replies = prompt_choice("include replies in search?", choices=["y", "n"], default="n")
        search_type = prompt_choice("search by", choices=["text", "user"], default="text")
        if search_type == "text":
            search_term = prompt_text("Enter search term: ")
            if search_term == None:
                raise Cancelled
            search_term = search_term.lower()
            filtered_comments = []
            for comment in comments:
                if search_term in comment["textOriginal"].lower():
                    filtered_comments.append(comment)
                    print(len(filtered_comments))
                else:
                    if include_replies == "y":
                        for reply in comment["replies"]:
                            if search_term in reply["textOriginal"].lower():
                                filtered_comments.append(comment)
                                print(len(filtered_comments))
                                break
        elif search_type == "user":
            search_term = prompt_text("Enter username").lower()
            filtered_comments = []
            for comment in comments:
                if search_term in comment["authorDisplayName"].lower():
                    filtered_comments.append(comment)
                    print(len(filtered_comments))
                else:
                    if include_replies == "y":
                        for reply in comment["replies"]:
                            if search_term in reply["authorDisplayName"].lower():
                                filtered_comments.append(comment)
                                print(len(filtered_comments))
                                break

        commentn = 0
        old = -1
        print("filtered comments")
    except Cancelled:
        os.system('cls')
        old = -1


def refresh_comments():
    global comments, filtered_comments, commentn, old, sort_pars
    os.system('cls')
    print("Refreshing comments...")
    comments = fetch_all_comments(video_id)
    with open(f"./videos/{video_id}.bin", "wb") as f:
        dill.dump(comments, f)
    filtered_comments = comments.copy()
    commentn = 0
    old = -1
    sort_pars = ["none"]
    os.system('cls')
    print("Refreshed comments")


def sort_comments():
    global filtered_comments, commentn, old, sort_pars
    os.system('cls')
    try:
        reverse = prompt_choice("reverse order?", choices=["y", "n"], default="n")
        sort_type = prompt_choice("sorts", choices=["likes", "replies", "update time", "publish time", "none"], default="none")
        if sort_type == "likes":
            filtered_comments = sorted(filtered_comments, key=lambda x: x['likeCount'], reverse=True)
            sort_pars = ["likes", "highest to lowest" if reverse == "n" else "lowest to highest"]
        elif sort_type == "replies":
            filtered_comments = sorted(filtered_comments, key=lambda x: len(x['replies']), reverse=True)
            sort_pars = ["replies", "highest to lowest" if reverse == "n" else "lowest to highest"]
        elif sort_type == "update time":
            filtered_comments = sorted(filtered_comments, key=lambda x: datetime.fromisoformat(x['updatedAt'].replace('Z', '+00:00')), reverse=True)
            sort_pars = ["update time", "newest to oldest" if reverse == "n" else "oldest to newest"]
        elif sort_type == "publish time":
            filtered_comments = sorted(filtered_comments, key=lambda x: datetime.fromisoformat(x['publishedAt'].replace('Z', '+00:00')), reverse=True)
            sort_pars = ["publish time", "newest to oldest" if reverse == "n" else "oldest to newest"]
        if reverse == "y":
            filtered_comments.reverse()
        commentn = 0
        old = -1
    except Cancelled:
        os.system('cls')
        old = -1


def show_help():
    global old
    os.system('cls')
    rprint("[bold underline]Controls[/bold underline]\n")
    rprint("[bold]w[/bold]          Previous comment")
    rprint("[bold]s[/bold]          Next comment")
    rprint("[bold]f[/bold]          Filter / search comments")
    rprint("[bold]e[/bold]          Sort comments")
    rprint("[bold]g[/bold]          Post actions  [dim](reply / comment / edit / delete)[/dim]")
    rprint("[bold]r[/bold]          Refresh comments from YouTube")
    rprint("[bold]c[/bold]          Change video  [dim](load from file or fetch)[/dim]")
    rprint("[bold]h[/bold]          Show this help screen")
    rprint("[bold]q[/bold]          Quit")
    rprint("\n[dim]Press any key to return...[/dim]")
    
    wait_kb = KeyBindings()
    @wait_kb.add(Keys.Any)
    def _(event):
        event.app.exit()

    wait_app = Application(
        key_bindings=wait_kb,
        layout=Layout(Window(FormattedTextControl(text=""), height=1)),
        full_screen=False,
    )
    wait_app.run()
    os.system('cls')
    old = -1



@kb.add("r")
async def _(event):
    await run_in_terminal(refresh_comments, in_executor=True)
    render()


@kb.add("f")
async def _(event):
    await run_in_terminal(filter_comments, in_executor=True)
    render()


@kb.add("g")
async def _(event):
    if filtered_empty_block():
        return
    await run_in_terminal(post_actions, in_executor=True)
    render()


@kb.add("c")
async def _(event):
    await run_in_terminal(change_video, in_executor=True)
    render()


@kb.add("w")
def _(event):
    global commentn
    if filtered_empty_block():
        return
    if commentn > 0:
        commentn += -1
    render()


@kb.add("s")
def _(event):
    global commentn
    if filtered_empty_block():
        return
    if commentn < len(filtered_comments) - 1:
        commentn += 1
    render()


@kb.add("q")
def _(event):
    if filtered_empty_block():
        return
    event.app.exit()


@kb.add("e")
async def _(event):
    if filtered_empty_block():
        return
    await run_in_terminal(sort_comments)
    render()


@kb.add("h")
async def _(event):
    if filtered_empty_block():
        return
    await run_in_terminal(show_help)
    render()


render()

main_app = Application(
    key_bindings=kb,
    layout=Layout(Window(FormattedTextControl(text=""), height=1)),
    full_screen=False,
)
main_app.run()