import re
import os
import sys
import json
import requests
import contextlib
import datetime
import calendar
import time
import logging
import subprocess

FORMAT = '%(asctime)s: %(message)s'
logging.basicConfig(format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO,  handlers=[
    logging.FileHandler("auto_sync.log", encoding = "UTF-8"),
    logging.StreamHandler()
])

def answer(equation):
    x = 0
    if '+' in equation:
        y = equation.split('+')
        x = int(y[0])+int(y[1])
    elif '−' in equation:
        y = equation.split('−')
        x = int(y[0])-int(y[1])
    return x

@contextlib.contextmanager
def open_editor(wikis):
    editors = { key:WikiEditor(wikis[key]) for key in wikis }
    for key in editors:
        editors[key].login()
    yield editors
    for key in editors:
        editors[key].logout()


class WikiEditor(object):

    def __init__ (self, info):
        self.info = info
        self.sess = None
    
    def login(self):
        self.sess = requests.Session()
        # Get Request to fetch login token
        para = {
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json"
        }
        res = self.sess.get(url=self.info["url"], params=para)
        data = res.json()
        tokens = data['query']['tokens']['logintoken']
        # Send a post request to login.
        para = {
            "action": "login",
            'lgname': self.info["botName"],
            'lgpassword': self.info["botPassword"],
            "lgtoken": tokens,
            "format": "json"
        }
        res = self.sess.post(url=self.info["url"], data=para)

    def logout(self):
        # GET request to fetch CSRF token
        para = {
            "action": "query",
            "meta": "tokens",
            "format": "json"
        }
        res = self.sess.get(url=self.info["url"], params=para)
        data = res.json()
        csrf_tokens = data['query']['tokens']['csrftoken']
        # Send a post request to logout.
        para = {
            "action": "logout",
            "token": csrf_tokens,
        }
        res = self.sess.get(url=self.info["url"], params=para)
        self.sess = None

    def query_recent_changes(self, target_date):
        response = requests.get(
            self.info["url"],
            params={
                'action': 'query',
                'format': 'json',
                'list': 'recentchanges',
                'rcstart': target_date.strftime("%Y-%m-%d") + 'T23:59:00Z', # why inverted?
                'rcend': target_date.strftime("%Y-%m-%d") + 'T00:00:00Z',    # why inverted?
                'rcprop': 'title|timestamp|user|comment',
                'rclimit': 500,
                'rctype': 'edit|new',
                'rcdir': 'older'
            }
        ).json()
        return response['query']['recentchanges']

    def query_page(self, title):
        response = requests.get(
            self.info["url"],
            params={
                'action': 'query',
                'format': 'json',
                'titles': title,
                'prop': 'revisions',
                'rvprop': 'timestamp|user|content|comment'
            }
        ).json()
        if '-1' in response['query']['pages']:
            return None
        page = next(iter(response['query']['pages'].values()))
        return page['revisions'][0]
    
    def check_success(self, res):
        data = res.json()
        if res.status_code != 200:
            return False, data
        if "edit" not in data:
            return False, data
        if "result" not in data["edit"]:
            return False, data
        if data["edit"]["result"] != "Success":
            return False, data
        return True, data
        
    def post_edit(self, title, srcCode, autobot_comment):
        # GET request to fetch CSRF token
        para = {
            "action": "query",
            "meta": "tokens",
            "format": "json"
        }
        res = self.sess.get(url=self.info["url"], params=para)
        data = res.json()
        csrf_tokens = data['query']['tokens']['csrftoken']
        # POST request to edit a page
        para = {
            "action": "edit",
            "title": title,
            "token": csrf_tokens,
            "format": "json",
            "text": srcCode,
            "watchlist": "unwatch",
            "summary": autobot_comment, 
            "bot": True
        }
        res = self.sess.post(url=self.info["url"], data=para)
        # check if captcha is needed
        suc, data = self.check_success(res)
        if not suc:
            if "captcha" in data["edit"]:
                captcha_id = data["edit"]["captcha"]["id"]
                captcha_q = data["edit"]["captcha"]["question"]
                ans = answer(captcha_q)
                para["captchaword"] = str(ans)
                para["captchaid"] = captcha_id
                res = self.sess.post(url=self.info["url"], data=para)
                suc, data = self.check_success(res)
        return suc, res


class WikiBackup():

    NON_SYNC_PREFFIX = {
        "首頁":"首頁",
        "檔案":"檔案",
        "使用者": "使用者專頁",
        "特殊":"特殊分頁",
        "討論:":"討論分頁",
        "模板:Mirrorpage":"模板:Mirrorpage",
        "模板:Synchro":"模板:Synchro" 
    }

    AUTOBOT_COMMENT = "Wiki-Bot Backup"

    DEFAULT_IGNORE = {
        "首頁":"首頁",
        "檔案":"檔案",
        "使用者": "使用者專頁",
        "特殊":"特殊分頁",
        "討論:":"討論分頁",
        "模板:Mirrorpage":"模板:Mirrorpage",
        "模板:Synchro":"模板:Synchro" 
    }

    def __init__ (self, config, logger):
        self.config = config
        self.wikis = config["wiki"]
        self.logger = logger

    def get_conf(self, name, default):
        if name not in self.config:
            return default
        return self.config[name]

    def back_up(self):
        # $env:PYTHONUTF8=1
        # $env:PYTHONIOENCODING='utf-8'
        os.environ["PYTHONUTF8"] = "1"
        os.environ["PYTHONIOENCODING"] = 'utf-8'
        def to_filename(title_name):
            title_name = title_name.replace("/", "%2F")
            title_name = title_name.replace(":", "%3A")
            return title_name + ".txt"                
        process = subprocess.Popen(["git", "pull"], shell=True,
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE)        
        for ln in process.stdout: 
            ln = ln.decode('utf-8')
            self.logger.info(ln.replace("\r\n", "").replace("\n", ""))
        push = False
        with open_editor(self.wikis) as editors:            
            for key in editors:       
                if not os.path.exists(os.path.join(".", key)):
                    os.mkdir(os.path.join(".", key))
                if not os.path.exists(os.path.join(".", key, "data")):
                    os.mkdir(os.path.join(".", key, "data"))         
                backup_lst = {}
                lst = editors[key].query_recent_changes(datetime.date.today() + datetime.timedelta(days = -1 * self.get_conf("backlog_day", 1)))
                for en in lst:           
                    if en["title"].startswith("使用者") or en["title"].startswith("討論"):
                        continue
                    # content = editors[key].query_page(en["title"])
                    if en["title"] not in backup_lst:
                        backup_lst[en["title"]] = to_filename(en["title"])                    
                for title in backup_lst:
                    self.logger.info("Back up page: {}".format(title))
                    content = editors[key].query_page(title)
                    file_path = os.path.join(".", key, "data", backup_lst[title])
                    with open(file_path, "w") as f:
                        f.write(content['*'])
                    # subprocess.run(["git", "add", file_path], check=False)
                    process = subprocess.Popen(["git", "add", file_path], shell=True,
                           stdout=subprocess.PIPE, 
                           stderr=subprocess.PIPE)
                    for ln in process.stdout: 
                        ln = ln.decode('utf-8')
                        self.logger.info(ln.replace("\r\n", ""))
                        self.logger.info(ln.replace("\n", ""))
                        self.logger.info(ln)
                process = subprocess.Popen(["git", "commit", "-m", "於 {} 自動備份 ".format(datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M"))], shell=True,
                           stdout=subprocess.PIPE, 
                           stderr=subprocess.PIPE)                
                for ln in process.stdout: 
                    ln = ln.decode('utf-8')
                    self.logger.info(ln.replace("\r\n", "").replace("\n", ""))                    
                    if "nothing added to commit" not in ln:
                        push = True
        if push:
            process = subprocess.Popen(["git", "push"], shell=True,
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE)        
            for ln in process.stdout: 
                ln = ln.decode('utf-8')
                self.logger.info(ln.replace("\r\n", "").replace("\n", ""))

if __name__ == "__main__":
    logger = logging.getLogger('wiki')

    # read config
    data = {}
    with open("config.json", "r", encoding="utf-8") as jsonfile:
        data = json.load(jsonfile)    

    if ("wiki" not in data) or (len(data["wiki"]) == 0):
        logger.error("設定錯誤: 沒有源頭")
        quit()

    logger.info("Back Up: {}".format(str([ data["wiki"][key]["name"] for key in data["wiki"] ])))
    
    operator = WikiBackup(data, logger)
    operator.back_up()

    