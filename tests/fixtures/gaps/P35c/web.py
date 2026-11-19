from flask import Blueprint

bp = Blueprint('b', __name__)

@bp.route('/')
def index_page():
    return 'x'
