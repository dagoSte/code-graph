from repo import Repo

class Service:
    def __init__(self, repo: Repo):
        self.repo = repo

    def go(self):
        return self.repo.save()
