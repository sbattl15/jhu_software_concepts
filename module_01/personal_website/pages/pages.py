bp = Blueprint('pages', __name__,
               template_foder='templates')
@bp.route('/')
def home():
    return render_template('home.html', active_page='home')

@bp.route('/contact')
def contact():
    return render_template('contact.html', active_page='contact')

@bp.route('/projects')
def projects():
    return render_template('projects.html', active_page='projects')

