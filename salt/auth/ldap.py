# -*- coding: utf-8 -*-
'''
Provide authentication using simple LDAP binds

:depends:   - ldap Python module
'''

# Import python libs
from __future__ import absolute_import
import logging

# Import salt libs
from salt.exceptions import CommandExecutionError, SaltInvocationError

log = logging.getLogger(__name__)

# Import third party libs
from jinja2 import Environment
try:
    import ldap
    import ldap.modlist
    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False

# Defaults, override in master config
__defopts__ = {'auth.ldap.server': 'localhost',
               'auth.ldap.port': '389',
               'auth.ldap.tls': False,
               'auth.ldap.no_verify': False,
               'auth.ldap.anonymous': False,
               'auth.ldap.scope': 2
               }


def _config(key, mandatory=True):
    '''
    Return a value for 'name' from master config file options or defaults.
    '''
    try:
        value = __opts__['auth.ldap.{0}'.format(key)]
    except KeyError:
        try:
            value = __defopts__['auth.ldap.{0}'.format(key)]
        except KeyError:
            if mandatory:
                msg = 'missing auth.ldap.{0} in master config'.format(key)
                raise SaltInvocationError(msg)
            return False
    return value


def _render_template(param, username):
    '''
    Render config template, substituting username where found.
    '''
    env = Environment()
    template = env.from_string(param)
    variables = {'username': username}
    return template.render(variables)


class _LDAPConnection(object):
    '''
    Setup an LDAP connection.
    '''

    def __init__(self, server, port, tls, no_verify, binddn, bindpw,
                 anonymous):
        '''
        Bind to an LDAP directory using passed credentials.
        '''
        self.server = server
        self.port = port
        self.tls = tls
        self.binddn = binddn
        self.bindpw = bindpw
        schema = 'ldap'
        if not HAS_LDAP:
            raise CommandExecutionError('Failed to connect to LDAP, module '
                                        'not loaded')
        try:
            if no_verify:
                ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                ldap.OPT_X_TLS_NEVER)
            if self.tls:
                schema = 'ldaps'
            self.ldap = ldap.initialize(
                '{0}://{1}:{2}'.format(schema, self.server, self.port)
            )
            self.ldap.protocol_version = 3  # ldap.VERSION3
            self.ldap.set_option(ldap.OPT_REFERRALS, 0)  # Needed for AD

            if not anonymous:
                self.ldap.simple_bind_s(self.binddn, self.bindpw)
        except Exception as ldap_error:
            raise CommandExecutionError(
                'Failed to bind to LDAP server {0}:{1} as {2}: {3}'.format(
                    self.server, self.port, self.binddn, ldap_error
                )
            )


def auth(username, password):
    '''
    Authenticate via an LDAP bind
    '''
    # Get config params; create connection dictionary
    basedn = _config('basedn')
    scope = _config('scope')
    connargs = {}
    # config params (auth.ldap.*)
    params = {
            'mandatory': ['server', 'port', 'tls', 'no_verify', 'anonymous'],
            'additional': ['binddn', 'bindpw', 'filter'],
    }

    paramvalues = {}

    for param in params['mandatory']:
        paramvalues[param] = _config(param)

    for param in params['additional']:
        paramvalues[param] = _config(param, mandatory=False)
        #try:
        #    paramvalues[param] = _config(param)
        #except SaltInvocationError:
        #    pass

    if paramvalues['binddn']:
        # the binddn can also be composited, e.g.
        #   - {{ username }}@domain.com
        #   - cn={{ username }},ou=users,dc=company,dc=tld
        # so make sure to render it first before using it
        paramvalues['binddn'] = _render_template(paramvalues['binddn'], username)

    if paramvalues['filter']:
        paramvalues['filter'] = _render_template(paramvalues['filter'], username)

    # Only add binddn/bindpw to the connargs when they're set, as they're not
    # mandatory for initializing the LDAP object, but if they're provided
    # initially, a bind attempt will be done during the initialization to
    # validate them
    if paramvalues['binddn']:
        connargs['binddn'] = paramvalues['binddn']
        if paramvalues['bindpw']:
            params['mandatory'].append('bindpw')

    for name in params['mandatory']:
        connargs[name] = paramvalues[name]

    if not paramvalues['anonymous']:
        if paramvalues['binddn'] and paramvalues['bindpw']:
            # search for the user's DN to be used for the actual authentication
            _ldap = _LDAPConnection(**connargs).ldap
            log.debug(
                'Running LDAP user dn search with filter:{0}, dn:{1}, '
                'scope:{2}'.format(
                    paramvalues['filter'], basedn, scope
                )
            )
            result = _ldap.search_s(basedn, int(scope), paramvalues['filter'])
            if len(result) < 1:
                log.warn('Unable to find user {0}'.format(username))
                return False
            elif len(result) > 1:
                log.warn('Found multiple results for user {0}'.format(username))
                return False
            connargs['binddn'] = result[0][0]
        if paramvalues['binddn'] and not paramvalues['bindpw']:
            connargs['binddn'] = paramvalues['binddn']
    elif paramvalues['binddn'] and not paramvalues['bindpw']:
        connargs['binddn'] = paramvalues['binddn']

    # Update connection dictionary with the user's password
    connargs['bindpw'] = password
    # Attempt bind with user dn and password
    log.debug('Attempting LDAP bind with user dn: {0}'.format(connargs['binddn']))
    try:
        _LDAPConnection(**connargs).ldap
    except Exception:
        #log.warn('Failed to authenticate user dn via LDAP: {0}'.format(connargs['binddn']))
        log.warn('Failed to authenticate user dn via LDAP: {0}'.format(connargs))
        return False
    log.debug(
        'Successfully authenticated user dn via LDAP: {0}'.format(
            connargs['binddn']
        )
    )
    return True
