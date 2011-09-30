# -*- coding: utf-8 -*-

"""
Copyright (C) 2010 Dariusz Suchojad <dsuch at gefira.pl>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# stdlib
import logging
from json import dumps
from traceback import format_exc
from uuid import uuid4

# Django
from django.http import HttpResponse, HttpResponseServerError
from django.shortcuts import render_to_response

# lxml
from lxml import etree
from lxml.objectify import Element

# Validate
from validate import is_boolean

# Zato
from zato.admin.web.forms import ChangePasswordForm, ChooseClusterForm
from zato.admin.web.forms.security.basic_auth import DefinitionForm
from zato.admin.web.views import change_password as _change_password, meth_allowed
from zato.common import zato_namespace, zato_path, ZatoException, ZATO_NOT_GIVEN
from zato.admin.web import invoke_admin_service
from zato.common.odb.model import Cluster, SSLBasicAuth, SSLBasicAuthItem
from zato.common import ZATO_FIELD_OPERATORS
from zato.common.util import TRACE1, to_form

logger = logging.getLogger(__name__)

ZATO_FORM_SEPARATOR = '***ZATO_FORM_SEPARATOR***'

def _get_definition_text(items):
    """ Takes a list of definition items returned by the backend and turns
    it into a pretty-printed HTML snippet.
    """
    out = []
    for item in items.getchildren():
        out.append('{0} {1} {2}'.format(
            item.field, ZATO_FIELD_OPERATORS[item.operator], item.value))
    return '<br/>'.join(out)

def _get_edit_create_message(params, prefix=''):
    """ Creates a base document which can be used by both 'edit' and 'create' actions.
    """
    
    zato_message = Element('{%s}zato_message' % zato_namespace)
    zato_message.data = Element('data')
    zato_message.data.id = params.get('id')
    zato_message.data.cluster_id = params['cluster_id']
    zato_message.data.name = params[prefix + 'name']
    zato_message.data.is_active = bool(params.get(prefix + 'is_active'))
    zato_message.data.def_items = Element('items')
    
    hidden = params.getlist('ssl-def-hidden')
    for elem in hidden:
        field, operator, value = elem.split(ZATO_FORM_SEPARATOR)
        item = Element('item')
        item.field = field
        item.operator = operator
        item.value = value
        zato_message.data.def_items.append(item)
        
    return zato_message

@meth_allowed('GET')
def index(req):

    zato_clusters = req.odb.query(Cluster).order_by('name').all()
    choose_cluster_form = ChooseClusterForm(zato_clusters, req.GET)
    cluster_id = req.GET.get('cluster')
    defs = []
    
    create_form = DefinitionForm()
    edit_form = DefinitionForm(prefix='edit')
    change_password_form = ChangePasswordForm()

    if cluster_id and req.method == 'GET':
        cluster = req.odb.query(Cluster).filter_by(id=cluster_id).first()

        zato_message = Element('{%s}zato_message' % zato_namespace)

        _ignored, zato_message, soap_response  = invoke_admin_service(cluster,
                'zato:security.ssl.get-list', zato_message)

        if zato_path('data.definition_list.definition').get_from(zato_message) is not None:
            for definition_elem in zato_message.data.definition_list.definition:

                id = definition_elem.id.text
                name = definition_elem.name.text
                is_active = is_boolean(definition_elem.is_active.text)
                
                definition_text = _get_definition_text(definition_elem.def_items)
                auth = SSLBasicAuth(id, name, is_active, definition_text=definition_text)

                defs.append(auth)

    return_data = {'zato_clusters':zato_clusters,
        'cluster_id':cluster_id,
        'choose_cluster_form':choose_cluster_form,
        'defs':defs,
        'create_form': create_form,
        'edit_form': edit_form,
        'change_password_form': change_password_form
        }

    # TODO: Should really be done by a decorator.
    if logger.isEnabledFor(TRACE1):
        logger.log(TRACE1, 'Returning render_to_response [%s]' % return_data)

    return render_to_response('zato/security/ssl.html', return_data)

@meth_allowed('POST')
def edit(req):
    """ Updates the HTTP Basic Auth definitions's parameters (everything except
    for the password).
    """
    try:
        cluster_id = req.POST.get('cluster_id')
        cluster = req.odb.query(Cluster).filter_by(id=cluster_id).first()
        zato_message = _get_edit_create_message(req.POST, prefix='edit-')

        _, zato_message, soap_response = invoke_admin_service(cluster,
                                    'zato:security.basic-auth.edit', zato_message)
    except Exception, e:
        msg = "Could not update the HTTP Basic Auth definition, e=[{e}]".format(e=format_exc(e))
        logger.error(msg)
        return HttpResponseServerError(msg)
    else:
        return HttpResponse()

@meth_allowed('POST')
def create(req):

    try:
        
        cluster_id = req.POST.get('cluster_id')
        cluster = req.odb.query(Cluster).filter_by(id=cluster_id).first()

        zato_message = _get_edit_create_message(req.POST)
        
        return_data = {'pk': 'zzz'}

        _, zato_message, soap_response = invoke_admin_service(cluster,
                            'zato:security.ssl.create', zato_message)

    except Exception, e:
        msg = "Could not create an SSL/TLS definition, e=[{e}]".format(e=format_exc(e))
        logger.error(msg)
        return HttpResponseServerError(msg)
    else:
        return_data = {'pk': zato_message.data.ssl.id.text}
        return HttpResponse(dumps(return_data), mimetype='application/javascript')
    
@meth_allowed('POST')
def format_item(req):

    field = req.POST['ssl-def-field']
    op_raw = req.POST['ssl-def-op']
    op = ZATO_FIELD_OPERATORS[op_raw]
    value = req.POST['ssl-def-value']
    id = uuid4().hex
    
    row_template = """
        <tr id="ssl-def-row-{id}">
            <td>{field}</td>
            <td>{op}</td>
            <td>{value}</td>
            <td>
                <a href="javascript:remove_from_def('{id}')" class="common">Remove from definition</a>
                <input type="hidden" name="ssl-def-hidden" value="{field}{sep}{op_raw}{sep}{value}" />
            </td>
        </tr>
    """.format(id=id, field=field, op=op, op_raw=op_raw, value=value, sep=ZATO_FORM_SEPARATOR)
    
    return HttpResponse(row_template)

@meth_allowed('POST')
def delete(req, id, cluster_id):
    
    cluster = req.odb.query(Cluster).filter_by(id=cluster_id).first()
    
    try:
        zato_message = Element('{%s}zato_message' % zato_namespace)
        zato_message.data = Element('data')
        zato_message.data.id = id
        
        _, zato_message, soap_response = invoke_admin_service(cluster,
                        'zato:security.basic-auth.delete', zato_message)
    
    except Exception, e:
        msg = "Could not delete the HTTP Basic Auth definition, e=[{e}]".format(e=format_exc(e))
        logger.error(msg)
        return HttpResponseServerError(msg)
    else:
        return HttpResponse()