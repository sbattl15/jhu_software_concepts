from flask import Flask

from class_app.pages import pages

def create_app():
    app = Flask(__name__)
    app.register_blueprint(pages.bp)
    return app