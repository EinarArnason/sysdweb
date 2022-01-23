#! /usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
#
# Copyright © 2016-2018 Óscar García Amor <ogarcia@connectical.com>
#
# Distributed under terms of the GNU GPLv3 license.

import os
from bottle import abort, request, response, route, run, static_file, template, TEMPLATE_PATH, HTTPError, mount, default_app
from pam import pam
from socket import gethostname
from datetime import datetime
from sysdweb.config import checkConfig
from sysdweb.systemd import systemdBus, Journal

# Search for template path
template_paths = [os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates'),
                  '/usr/share/sysdweb/templates']
template_path = [path for path in template_paths if os.access(path, os.R_OK)]
if template_path == []:
    raise SystemExit('Templates are missing.')
TEMPLATE_PATH.insert(0, os.path.join(template_path[0], 'views'))
static_path = os.path.join(template_path[0], 'static')

# Define auth function


def login(user, password):
    users = config.get('DEFAULT', 'users', fallback=None)
    auth = config.get('DEFAULT', 'auth', fallback=None)

    if auth == 'basic':
        userlist = [tuple(usr.strip().split(':')) for usr in users.split(',')]
        return True if (user,password) in userlist else False
    elif auth == 'pam':
        if users and user not in users.split(','): return False
        return pam().authenticate(user, password)
    elif auth == 'none':
        return True

    return False

def if_auth(check, realm="private", text="Access denied"):
    """ Callback decorator to require HTTP auth (basic).
        TODO: Add route(check_auth=...) parameter. """

    def decorator(func):
        def wrapper(*a, **ka):
            auth = config.get('DEFAULT', 'auth', fallback=None)

            if auth == 'none': return func(*a, **ka)

            user, password = request.auth or (None, None)

            if user is None or not check(user, password):
                err = HTTPError(401, text)
                err.add_header('WWW-Authenticate', 'Basic realm="%s"' % realm)
                return err

            return func(*a, **ka)

        return wrapper

    return decorator

@route('/api/v1/services')
@if_auth(login)
def get_service_list():
    return {'services': [service for service in config.sections() if service != 'DEFAULT']}


@route('/api/v1/<service>/<action>')
@if_auth(login)
def get_service_action(service, action):
    if service in config.sections():
        sdbus = systemdBus(True) if config.get(
            'DEFAULT', 'scope', fallback='system') == 'user' else systemdBus()
        unit = config.get(service, 'unit')
        if action == 'start':
            return {action: 'OK'} if sdbus.start_unit(unit) else {action: 'Fail'}
        elif action == 'stop':
            return {action: 'OK'} if sdbus.stop_unit(unit) else {action: 'Fail'}
        elif action == 'restart':
            return {action: 'OK'} if sdbus.restart_unit(unit) else {action: 'Fail'}
        elif action == 'reload':
            return {action: 'OK'} if sdbus.reload_unit(unit) else {action: 'Fail'}
        elif action == 'reloadorrestart':
            return {action: 'OK'} if sdbus.reload_or_restart_unit(unit) else {action: 'Fail'}
        elif action == 'status':
            if sdbus.get_unit_load_state(unit) != 'not-found':
                return {action: str(sdbus.get_unit_active_state(unit))}
            else:
                return {action: 'not-found'}
        elif action == 'uptime':
            if sdbus.get_unit_load_state(unit) != 'not-found':
                unixtime = int(sdbus.get_unit_uptime(unit))
                uptime_s = datetime.now() - datetime.fromtimestamp(unixtime/1000000)
                return {action: str(uptime_s)}
            else:
                return {action: 'not-found'}
        elif action == 'journal':
            return get_service_journal(service, 100)
        else:
            response.status = 400
            return {'msg': 'Sorry, but cannot perform \'{}\' action.'.format(action)}
    else:
        response.status = 400
        return {'msg': 'Sorry, but \'{}\' is not defined in config.'.format(service)}


@route('/api/v1/<service>/journal/<lines>')
@if_auth(login)
def get_service_journal(service, lines):
    if service in config.sections():
        if get_service_action(service, 'status')['status'] == 'not-found':
            return {'journal': 'not-found'}
        try:
            lines = int(lines)
        except Exception as e:
            response.status = 500
            return {'msg': '{}'.format(e)}
        unit = config.get(service, 'unit')
        journal = Journal(unit)
        return {'journal': journal.get_tail(lines)}
    else:
        response.status = 400
        return {'msg': 'Sorry, but \'{}\' is not defined in config.'.format(service)}


@route('/')
@if_auth(login)
def get_main():
    services = []
    for service in config.sections():
        service_status = get_service_action(service, 'status')
        service_uptime = get_service_action(service, 'uptime')

        if service_status['status'] == 'not-found':
            cls = 'active'
        elif service_status['status'] == 'inactive' or service_status['status'] == 'failed':
            cls = 'danger'
        elif service_status['status'] == 'active':
            cls = 'success'
        else:
            cls = 'warning'
        disabled_start = True if cls == 'active' or cls == 'success' else False
        disabled_stop = True if cls == 'active' or cls == 'danger' else False
        disabled_restart = True if cls == 'active' or cls == 'danger' else False
        service_uptime = service_uptime['uptime'] if cls == 'active' or cls == 'success' else '00:00:00'
        services.append({'class': cls,
            'disabled_start': disabled_start,
            'disabled_stop': disabled_stop,
            'disabled_restart': disabled_restart,
            'title': config.get(service, 'title'),
            'uptime': service_uptime,
            'service': service})
    return template('index', hostname=gethostname(), services=services)


@route('/journal/<service>')
@if_auth(login)
def get_service_journal_page(service):
    if service in config.sections():
        if get_service_action(service, 'status')['status'] == 'not-found':
            abort(400, 'Sorry, but service \'{}\' unit not found in system.'.format(
                config.get(service, 'title')))
        journal_lines = get_service_journal(service, 100)
        return template('journal', hostname=gethostname(), service=config.get(service, 'title'), journal=journal_lines['journal'])
    else:
        abort(400, 'Sorry, but \'{}\' is not defined in config.'.format(service))

# Serve static content
@route('/favicon.ico')
@if_auth(login)
def get_favicon():
    return static_file('favicon.ico', root=os.path.join(static_path, 'img'))


@route('/css/<file>')
@if_auth(login)
def get_css(file):
    return static_file(file, root=os.path.join(static_path, 'css'))


@route('/fonts/<file>')
@if_auth(login)
def get_fonts(file):
    return static_file(file, root=os.path.join(static_path, 'fonts'))


@route('/img/<file>')
@if_auth(login)
def get_img(file):
    return static_file(file, root=os.path.join(static_path, 'img'))


@route('/js/<file>')
@if_auth(login)
def get_js(file):
    return static_file(file, root=os.path.join(static_path, 'js'))


def start(config_file, host, port):
    # Check config
    global config
    config = checkConfig(config_file)

    if host == None:
        host = config.get('DEFAULT', 'host', fallback='127.0.0.1')
    if port == None:
        port = config.get('DEFAULT', 'port', fallback='8085')

    # Run webserver
    root = config.get('DEFAULT', 'root', fallback=None)
    if (root != None):
        mount(root, default_app())
    run(host=host, port=port)
