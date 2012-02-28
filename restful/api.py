# -*- coding: utf-8; Mode: Python -*-
#
# Copyright (C) 2011  Lincoln de Sousa <lincoln@comum.org>
# Copyright 2012 Jeffrey Finkelstein <jeffrey.finkelstein@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
    restful.api
    ~~~~~~~~~~~

    Defines a flask model that provides a generic model API to be
    exposed using REST spec.

    This module is intended to be a generic way for creating, updating,
    searching and deleting entries of an sqlalchemy model (declared with
    elixir) through a generic HTTP API.

    All models being exposed by this API are going to be validated using
    formencode validators that should be declared by the user of this
    library.

    :copyright:2011 by Lincoln de Sousa <lincoln@comum.org>
    :copyright:2012 Jeffrey Finkelstein <jeffrey.finkelstein@gmail.com>
    :license: AGPLv3, see COPYTING for more details
"""

from collections import namedtuple
import json

from dateutil.parser import parse as parse_datetime
from elixir import session
from flask import abort
from flask import Blueprint
from flask import jsonify
from flask import make_response
from flask import request
from flask.views import MethodView
from sqlalchemy.orm.exc import MultipleResultsFound
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql import func
from sqlalchemy.types import Date
from sqlalchemy.types import DateTime

from .model import get_or_create


__all__ = 'APIManager',

OPERATORS = {
    '==': lambda f, a, fn: f == a,
    'eq': lambda f, a, fn: f == a,
    'equals': lambda f, a, fn: f == a,
    'equal_to': lambda f, a, fn: f == a,
    '!=': lambda f, a, fn: f != a,
    'neq': lambda f, a, fn: f != a,
    'not_equal_to': lambda f, a, fn: f != a,
    'does_not_equal': lambda f, a, fn: f != a,
    '>': lambda f, a, fn: f > a,
    'gt': lambda f, a, fn: f > a,
    '<': lambda f, a, fn: f < a,
    'lt': lambda f, a, fn: f < a,
    '>=': lambda f, a, fn: f >= a,
    'gte': lambda f, a, fn: f >= a,
    '<=': lambda f, a, fn: f <= a,
    'lte': lambda f, a, fn: f <= a,
    'like': lambda f, a, fn: f.like(a),
    'in': lambda f, a, fn: f.in_(a),
    'not_in': lambda f, a, fn: ~f.in_(a),
    'is_null': lambda f, a, fn: f == None,
    'is_not_null': lambda f, a, fn: f != None,
    'desc': lambda f, a, fn: f.desc,
    'asc': lambda f, a, fn: f.asc,
    'has': lambda f, a, fn: f.has(**{fn: a}),
    'any': lambda f, a, fn: f.any(**{fn: a})
}
"""The mapping from operator name (as accepted by the search method) to a
function which returns the SQLAlchemy expression corresponding to that
operator.

The function in each of the values takes three arguments. The first argument is
the field object on which to apply the operator. The second argument is the
second argument to the operator, should one exist. The third argument is the
name of the field. All functions use the first argument, some use the second,
and few use the third.

Some operations have multiple names. For example, the equality operation can be
described by the strings ``'=='``, ``'eq'``, ``'equals'``, etc.

"""

Function = namedtuple('Function', ['name', 'field'])
Function.__str__ = lambda self: '{}__{}'.format(self.name, self.field)

class OrderBy(object):
    def __init__(self, field, direction='asc'):
        self.field = field
        self.direction = direction
    def __repr__(self):
        return '<OrderBy {}, {}>'.format(self.field, self.direction)


class IllegalArgumentError(Exception): pass

class Filter(object):
    ILLEGAL_ARG_MSG = 'Must specify exactly one of argument and otherfield'
    def __init__(self, fieldname, operator, argument=None, otherfield=None):
        if (argument and otherfield) or not (argument or otherfield):
            raise IllegalArgumentError(ILLEGAL_ARG_MSG)
        self.fieldname = fieldname
        self.operator = operator
        self.argument = argument
        self.otherfield = otherfield

    def __repr__(self):
        return '<Filter {} {} {}>'.format(self.fieldname, self.operator,
                                          self.argument or self.otherfield)

    @staticmethod
    def from_dictionary(dictionary):
        fieldname = dictionary.get('name')
        operator = dictionary.get('op')
        argument = dictionary.get('val')
        otherfield = dictionary.get('field')
        return Filter(fieldname, operator, argument, otherfield)


class SearchParameters(object):
    ILLEGAL_ARG_MSG = 'Must specify at most one of filters and functions'
    def __init__(self, filters=[], functions=[], searchtype=None, limit=None,
                 offset=None, order_by=None):
        if filters and functions:
            raise IllegalArgumentException(ILLEGAL_ARG_MSG)
        self.filters = filters
        self.functions = functions
        self.searchtype = searchtype
        self.limit = limit
        self.offset = offset
        self.order_by = order_by

    def __repr__(self):
        return '<SearchParameters filters={}, functions={}, order_by={}, limit={}, offset={}, type={}>'.format(self.filters, self.functions, self.order_by, self.limit, self.offset, self.searchtype)

    @staticmethod
    def from_string(string):
        # may raise exception
        return self.from_dictionary(json.dumps(string))

    @staticmethod
    def from_dictionary(dictionary):
        filters = [Filter.from_dictionary(f) \
                       for f in dictionary.get('filters', [])]
        functions = [Function.from_dictionary(f) \
                         for f in dictionary.get('functions', [])]
        order_by = [OrderBy(**o) for o in dictionary.get('order_by', [])]
        searchtype = dictionary.get('type')
        limit = dictionary.get('limit')
        offset = dictionary.get('offset')
        return SearchParameters(filters=filters, functions=functions,
                                searchtype=searchtype, limit=limit,
                                offset=offset, order_by=order_by)


def search(model, search_params):
    """Performs the search specified by the given parameters on the model
    specified in the constructor of this class.

    ``search_params`` is a dictionary containing all available search
    parameters. (Implementation note: the real job of this function is to
    look for all search parameters of this dictionary and evaluate a
    SQLAlchemy query built with these arguments.) For more information on
    available search parameters, see :ref:`search`.

    If ``search_params`` specifies functions to execute on the matched data,
    the result of evaluating those functions will be returned instead of the
    matching instances.

    If ``search_params`` specifies that the type of the search is
    ``'one'``, then this method will raise
    :exc:`sqlalchemy.orm.exc.NoResultFound` if no results are found and
    :exc:`sqlalchemy.orm.exc.MultipleResultsFound` if multiple results are
    found.

    """
    # Aplying functions
    if search_params.functions:
        return _evaluate_functions(model, search_params.functions)

    # Building the query
    query = create_query(model, search_params)

    relations = model.get_relations()
    deep = dict(zip(relations, [{}] * len(relations)))
    if search_params.searchtype == 'one':
        # may raise NoResultFound or MultipleResultsFound
        return query.one().to_dict(deep)
    else:
        return [x.to_dict(deep) for x in query.all()]


def _evaluate_functions(model, functions):
    """Executes the each of the SQLAlchemy functions specified in
    ``functions``, a list of instances of the :class:`Function` class, on
    the model specified in the constructor of this class and returns a
    dictionary mapping function name (slightly modified, see below) to
    result of evaluation of that function.

    ``functions`` is a list of :class:`Function` objects. For example, if
    you want the sum and the average of a field::

        >>> search = SearchManager(MyModel)
        >>> f1 = Function('sum', 'amount')
        >>> f2 = Function('avg', 'amount')
        >>> search._evaluate_functions(f1, f2)
        {'avg__amount': 456, 'sum__amount': 123}

    The return value is a dictionary mapping ``'<funcname>__<fieldname>'``
    to the result of evaluating that function on that field.

    """
    processed = []
    funcnames = []
    for f in functions:
        # We retrieve the function by name from the SQLAlchemy ``func``
        # module and the field by name from the model class.
        funcobj = getattr(func, f.name)
        field = getattr(model, f.field)
        # Time to store things to be executed. The processed list stores
        # functions that will be executed in the database and funcnames
        # contains names of the entries that will be returned to the
        # caller.
        processed.append(funcobj(field))
        funcnames.append(str(f))
    # evaluate all the functions at once and get an iterable of results
    evaluated = session.query(*processed).one()
    return dict(zip(funcnames, evaluated))


class QueryBuilder(object):

    @staticmethod
    def _create_operation(model, fieldname, operator, argument, relation=None):
        """Translates an operation described as a string to a valid SQLAlchemy
        query parameter using a field or relation of the specified model.

        More specifically, this translates the string representation of an
        operation, for example ``'gt'``, to an expression corresponding to a
        SQLAlchemy expression, ``field > argument``. The recognized operators
        are given by the keys of :data:`OPERATORS`.

        If ``relation`` is not ``None``, the returned search parameter will
        correspond to a search on the field named ``fieldname`` on the entity
        related to ``model`` whose name, as a string, is ``relation``.

        ``model`` is an instance of a :class:`elixir.entity.Entity` being
        searched.

        ``fieldname`` is the name of the field of ``model`` to which the
        operation will be applied as part of the search. If ``relation`` is
        specified, the operation will be applied to the field with name
        ``fieldname`` on the entity related to ``model`` whose name, as a
        string, is ``relation``.

        ``operation`` is a string representating the operation which will be
         executed between the field and the argument received. For example,
         ``'gt'``, ``'lt'``, ``'like'``, ``'in'`` etc.

        ``argument`` is the argument to which to apply the ``operator``.

        ``relation`` is the name of the relationship attribute of ``model`` to
        which the operation will be applied as part of the search, or ``None``
        if this function should not use a related entity in the search.

        """
        field = getattr(model, relation or fieldname)
        return OPERATORS.get(operator)(field, argument, fieldname)


    @staticmethod
    def _extract_operators(model, search_params):
        """Returns the list of operations on ``model`` specified in the
        :attr:`filters` attribute on the ``search_params`` object.

        ``search_params`` is an instance of the :class:`SearchParameters`
        class whose fields represent the parameters of the search.

        """
        operations = []
        for f in search_params.filters:
            fname = f.fieldname
            val = f.argument

            # get the relationship from the field name, if it exists
            # TODO document that field names must not contain "__"
            relation = None
            if '__' in fname:
                relation, fname = fname.split('__')

            # for the sake of brevity
            makeop = QueryBuilder._create_operation
            # We are going to compare a field with another one, so there's
            # no reason to parse
            if f.otherfield:
                otherfield = getattr(model, f.otherfield)
                param = makeop(model, fname, f.operator, otherfield, relation)
                operations.append(param)
                continue

            # Collecting the query
            param = makeop(model, fname, f.operator, val, relation)
            operations.append(param)

        return operations

    @staticmethod
    def create_query(model, search_params):
        """Builds an SQLAlchemy query instance based on the search parameters
        present in ``search_params``, an instance of :class:`SearchParameters`.

        This method returns a SQLAlchemy query in which all matched instances
        meet the requirements specified in ``search_params``.

        ``model`` is an :class:`elixir.entity.Entity` on which to create a
        query.

        ``search_params`` is an instance of :class:`SearchParameters` which
        specify the filters, order, limit, offset, etc. of the query.

        Building the query proceeds in this order:
        1. filtering the query
        2. ordering the query
        3. limiting the query
        4. offsetting the query

        """
        # Adding field filters
        query = model.query
        operations = QueryBuilder._extract_operators(model, search_params)
        for i in operations:
            query = query.filter(i)

        # Order the search
        for val in search_params.order_by:
            field = getattr(model, val.field)
            direction = getattr(field, val.direction)
            query = query.order_by(direction())

        # Limit it
        if search_params.limit:
            query = query.limit(search_params.limit)
        if search_params.offset:
            query = query.offset(search_params.offset)
        return query


def create_query(model, searchparams):
    if isinstance(searchparams, dict):
        searchparams = SearchParameters.from_dictionary(searchparams)
    elif isinstance(searchparams, str):
        # may raise exception
        searchparams = SearchParameters.from_string(searchparams)
    return QueryBuilder.create_query(model, searchparams)


def jsonify_status_code(status_code, *args, **kw):
    """Returns a jsonified response with the specified HTTP status code.

    The positional and keyword arguments are passed directly to the
    :func:`flask.jsonify` function which creates the response.

    """
    response = jsonify(*args, **kw)
    response.status_code = status_code
    return response


class API(MethodView):
    """Provides method-based dispatching for :http:request:`get`,
    :http:request:`post`, :http:request:`patch`, and :http:request:`delete`
    requests, for both collections of models and individual models.

    """

    def __init__(self, model, *args, **kw):
        """Calls the constructor of the superclass and specifies the model for
        which this class provides a ReSTful API.

        ``model`` is the :class:`elixir.entity.Entity` class of the database
        model for which this instance of the class is an API.

        """
        super(API, self).__init__(*args, **kw)
        self.model = model

    # TODO change this to have more sensible arguments
    def _update_relations(self, query, params):
        """Adds or removes models which are related to the model specified in
        the constructor of this class.

        ``query`` is a SQLAlchemy query instance that evaluates to all
        instances of the model specified in the constructor of this class that
        should be updated.

        `params`

            A dictionary with two keys ``add`` and ``remove``. Both of them
            contains a list of items that should be added or removed from
            such a relation.
        """
        fields = []
        cols = self.model.get_columns()
        relations = self.model.get_relations()
        for col in set(relations).intersection(params.keys()):
            submodel = cols[col].property.mapper.class_
            from_request = params[col]

            # Let's add new columns to the relation being managed.
            for subparams in from_request.get('add', ()):
                if 'id' in subparams:
                    subinst = submodel.get_by(id=subparams.pop('id'))
                else:
                    vssubparams = subparams
                    subinst = get_or_create(submodel, **vssubparams)[0]
                for instance in query:
                    getattr(instance, col).append(subinst)

            # Now is the time to handle relations being removed from a field
            # of the instance. We'll do nothing here if there's no id param
            for subparams in from_request.get('remove', ()):
                try:
                    remove = subparams.pop('__delete__')
                except KeyError:
                    remove = False
                if 'id' in subparams:
                    subinst = submodel.get_by(id=subparams['id'])
                else:
                    vssubparams = subparams
                    subinst = submodel.get_by(**vssubparams)
                for instance in query:
                    field = getattr(instance, col)
                    field.remove(subinst)
                if remove:
                    subinst.delete()

            fields.append(col)
        return fields

    def _search(self):
        """Defines a generic search function for the database model.

        If the query string is empty, or if the specified query is invalid for
        some reason (for example, searching for all person instances with), the
        response will be the JSON string ``{"objects": []}``.

        To search for entities meeting some criteria, the client makes a
        request to :http:get:`/api/<modelname>` with a query string containing
        the parameters of the search. The parameters of the search can involve
        filters and/or functions. In a filter, the client specifies the name of
        the field by which to filter, the operation to perform on the field,
        and the value which is the argument to that operation. In a function,
        the client specifies the name of a SQL function which is executed on
        the search results; the result of executing the function is returned to
        the client.

        The parameters of the search must be provided in JSON form as the value
        of the ``q`` request query parameter. For example, in a database of
        people, to search for all people with a name containing a "y", the
        client would make a :http:method:`get` request to ``/api/person`` with
        query parameter as follows::

            q={"filters": [{"name": "name", "op": "like", "val": "%y%"}]}

        If multiple objects meet the criteria of the search, the response has
        :http:status:`200` and content of the form::

            {"objects": [{"name": "Mary"}, {"name": "Byron"}, ...]}

        If the result of the search is a single instance of the model, the JSON
        representation of that instance would be the top-level object in the
        content of the response::

            {"name": "Mary", ...}

        For more information SQLAlchemy functions and operators for use in
        filters, see the `SQLAlchemy SQL expression tutorial
        <http://docs.sqlalchemy.org/en/latest/core/tutorial.html>`_.

        The general structure of request data as a JSON string is as follows::

            {
              "type": "one",
              "order_by": [{"field": "age", "direction": "asc"}],
              "limit": 2,
              "offset": 1,
              "filters":
                [
                  {"name": "name", "val": "%y%", "op": "like"},
                  {"name": "age", "val": [18, 19, 20, 21], "op": "in"},
                  {"name": "age", "op": "gt", "field": "height"},
                  ...
                ],
              "functions":
                [
                  {"name": "sum", "field": "age"},
                  ...
                ]
            }

        For a complete description of all possible search parameters, see
        :ref:`search`.

        """
        # try to get search query from the request query parameters
        try:
            data = json.loads(request.args.get('q', '{}'))
        except (TypeError, ValueError, OverflowError):
            return jsonify_status_code(400, message='Unable to decode data')

        # If the query parameters specify that at least one function should be
        # executed, return the result of executing that function. If no
        # functions are specified, perform a search and return the instances
        # which match that search.
        if 'functions' in data:
            # TODO data['functions'] may be poorly defined here...
            functions = [Function(**f) for f in data['functions']]
            result = _evaluate_functions(self.model, functions)
        else:
            try:
                searchparams = SearchParameters.from_dictionary(data)
                result = search(self.model, searchparams)
            except NoResultFound:
                return jsonify(message='No result found')
            except MultipleResultsFound:
                return jsonify(message='Multiple results found')

        # for security purposes, don't transmit list as top-level JSON
        if isinstance(result, list):
            return jsonify(objects=result)
        else:
            return jsonify(result)

    # TODO should this exist?
    def _patch_many(self):
        """Updates each of the instances of the model which match the query
        string.

        The ``request.data`` field should be filled with a JSON string that
        contains an object with two fields: query and form. The first one
        should have an object that looks the same as the one passed to the
        ``search`` method. The second field (form) should contain an object
        with all fields that will be passed to the ``update()`` method.

        """
        # TODO this code is common to patch
        try:
            data = json.loads(request.data)
        except (TypeError, ValueError, OverflowError):
            return jsonify_status_code(400, message='Unable to decode data')
        # build the query dictionary from the request query string
        query = create_query(self.model, data)

        # TODO this code is common to patch
        if len(data) == 0:
            return make_response(None, 204)

        # TODO this code is common to patch
        relations = set(self._update_relations(query, data))
        field_list = set(data.keys()) ^ relations
        originalparams = dict((field, data[field]) for field in field_list)

        # Special case: if there are any dates, convert the string form of the
        # date into an instance of the Python ``datetime`` object.
        params = {}
        for key, val in originalparams.iteritems():
            fieldtype = getattr(self.model, key).property.columns[0].type
            if isinstance(fieldtype, Date) or isinstance(fieldtype, DateTime):
                params[key] = parse_datetime(val)
            else:
                params[key] = val

        # Let's update all instances present in the query
        num_modified = 0
        if params:
            num_modified = query.update(params, False)
        session.commit()

        return jsonify(num_modified=num_modified)

    def get(self, instid):
        """Returns a JSON representation of an instance of model with the
        specified name.

        If ``instid`` is ``None``, this method returns the result of a search
        with parameters specified in the query string of the request. If no
        search parameters are specified, this method returns all instances of
        the specified model.

        If ``instid`` is an integer, this method returns the instance of the
        model with that identifying integer. (Implementation note: the
        underlying implementation uses the :func:`elixir.entity.Entity.get_by`
        method.) If no such instance exists, this method responds with
        :http:status:`404`.

        """
        if instid is None:
            return self._search()
        inst = self.model.get_by(id=instid)
        if inst is None:
            abort(404)
        relations = self.model.get_relations()
        deep = dict(zip(relations, [{} for n in range(len(relations))]))
        return jsonify(inst.to_dict(deep))

    def delete(self, instid):
        """Removes the specified instance of the model with the specified name
        from the database.

        Since :http:method:`delete` is an idempotent method according to the
        :rfc:`2616`, this method responds with :http:status:`204` regardless of
        whether an object was deleted.

        """
        inst = self.model.get_by(id=instid)
        if inst is not None:
            inst.delete()
            session.commit()
        return make_response(None, 204)

    def post(self):
        """Creates a new instance of a given model based on request data.

        This function parses the string contained in
        :attr:`flask.request.data`` as a JSON object and then validates it with
        a validator specified in the constructor of this class.

        The :attr:`flask.request.data` attribute will be parsed as a JSON
        object containing the mapping from field name to value to which to
        initialize the created instance of the model.

        After that, it separates all columns that defines relationships with
        other entities, creates a model with the simple columns and then
        creates instances of these submodels and associates them with the
        related fields. This happens only at the first level of nesting.

        Currently, this method can only handle instantiating a model with a
        single level of relationship data.

        """
        # try to read the parameters for the model from the body of the request
        try:
            params = json.loads(request.data)
        except (TypeError, ValueError, OverflowError):
            return jsonify_status_code(400, message='Unable to decode data')

        # Getting the list of relations that will be added later
        cols = self.model.get_columns()
        relations = self.model.get_relations()

        # Looking for what we're going to set on the model right now
        colkeys = cols.keys()
        paramkeys = params.keys()
        props = set(colkeys).intersection(paramkeys).difference(relations)

        # Instantiate the model with the parameters
        instance = self.model(**dict([(i, params[i]) for i in props]))

        # Handling relations, a single level is allowed
        for col in set(relations).intersection(paramkeys):
            submodel = cols[col].property.mapper.class_
            for subparams in params[col]:
                subinst = get_or_create(submodel, **subparams)[0]
                getattr(instance, col).append(subinst)

        # add the created model to the session
        session.add(instance)
        session.commit()

        return jsonify_status_code(201, id=instance.id)

    def patch(self, instid):
        """Updates the instance specified by ``instid`` of the named model, or
        updates multiple instances if ``instid`` is ``None``.

        The :attr:`flask.request.data` attribute will be parsed as a JSON
        object containing the mapping from field name to value to which to
        update the specified instance or instances.

        If ``instid`` is ``None``, the query string will be used to search for
        instances (using the :func:`_search` method), and all matching
        instances will be updated according to the content of the request data.
        See the :func:`_search` documentation on more information about search
        parameters for restricting the set of instances on which updates will
        be made in this case.

        """
        # TODO this code is common to _patch_many
        # if no instance is specified, try to patch many using a search
        if instid is None:
            return self._patch_many()
        
        # try to load the fields/values to update from the body of the request
        try:
            data = json.loads(request.data)
        except (TypeError, ValueError, OverflowError):
            return jsonify_status_code(400, message='Unable to decode data')

        # TODO this code is common to _patch_many
        # If there is no data to update, just return HTTP 204 No Content.
        if len(data) == 0:
            return make_response(None, 204)

        # TODO this code is common to _patch_many
        relations = set(self._update_relations([self.model.get_by(id=instid)],
                                               data))
        field_list = set(data.keys()) ^ relations
        originalparams = dict((field, data[field]) for field in field_list)

        # Special case: if there are any dates, convert the string form of the
        # date into an instance of the Python ``datetime`` object.
        params = {}
        for key, val in originalparams.iteritems():
            fieldtype = getattr(self.model, key).property.columns[0].type
            if isinstance(fieldtype, Date) or isinstance(fieldtype, DateTime):
                params[key] = parse_datetime(val)
            else:
                params[key] = val

        # Let's update field by field
        # TODO can this be made the same as in _patch_many?
        inst = self.model.get_by(id=instid)
        for field in field_list:
            setattr(inst, field, params[field])
        session.commit()

        # return the updated object
        return self.get(instid)


class APIManager(object):
    """TODO fill me in."""

    def __init__(self, app):
        """TODO fill me in.

        ``app`` is the :class:`flask.Flask` object containing the user's Flask
        application.

        """
        self.app = app

    # alternately: def add_api(modelname, readonly=True):
    def create_api(self, model, methods=['GET'], url_prefix='/api'):
        """Creates a ReSTful API interface as a blueprint and registers it on
        the :class:`flask.Flask` application specified in the constructor to
        this class.

        The endpoints for the API for ``model`` will be available at
        ``<url_prefix>/<modelname>``, where ``<url_prefix>`` is the last
        parameter to this function and ``<modelname>`` is the lowercase string
        representation of the model class, as accessed by
        ``model.__name__``. (If any black magic was performed on
        ``model.__name__``, this will be reflected in the endpoint URL.)

        This function must be called at most once for each model for which you
        wish to create a ReSTful API. Its behavior (for now) is undefined if
        called more than once.

        ``model`` is the :class:`elixir.entity.Entity` class for which a
        ReSTful interface will be created. Note this must be a class, not an
        instance of a class.

        ``methods`` specify the HTTP methods which will be made available on
        the ReSTful API for the specified model, subject to the following
        caveats:
        * If :http:method:`get` is in this list, the API will allow getting a
          single instance of the model, getting all instances of the model, and
          searching the model using search parameters.
        * If :http:method:`patch` is in this list, the API will allow updating
          a single instance of the model, updating all instances of the model,
          and updating a subset of all instances of the model specified using
          search parameters.
        * If :http:method:`delete` is in this list, the API will allow deletion
          of a single instance of the model per request.
        * If :http:method:`post` is in this list, the API will allow posting a
          new instance of the model per request.
        The default list of methods provides a read-only interface (that is,
        only :http:method:`get` requests are allowed).

        ``url_prefix`` specifies the URL prefix at which this API will be
        accessible.

        """
        modelname = model.__name__
        methods = frozenset(methods)
        # sets of methods used for different types of endpoints
        no_instance_methods = methods & {'POST'}
        possibly_empty_instance_methods = methods & {'GET', 'PATCH'}
        instance_methods = methods & {'GET', 'PATCH', 'DELETE'}
        # the base URL of the endpoints on which requests will be made
        collection_endpoint = '/{}'.format(modelname)
        instance_endpoint = collection_endpoint + '/<int:instid>'
        # the name of the API, for use in creating the view and the blueprint
        apiname = '{}api'.format(modelname)
        # the view function for the API for this model
        api_view = API.as_view(apiname, model)
        # add the URL rules to the blueprint: the first is for methods on the
        # collection only, the second is for methods which may or may not
        # specify an instance, the third is for methods which must specify an
        # instance
        # TODO what should the second argument here be?
        # TODO should the url_prefix be specified here or in register_blueprint
        blueprint = Blueprint(apiname, __name__, url_prefix=url_prefix)
        blueprint.add_url_rule(collection_endpoint,
                               methods=no_instance_methods, view_func=api_view)
        blueprint.add_url_rule(collection_endpoint, defaults={'instid': None},
                               methods=possibly_empty_instance_methods,
                               view_func=api_view)
        blueprint.add_url_rule(instance_endpoint, methods=instance_methods,
                               view_func=api_view)
        # register the blueprint on the app
        self.app.register_blueprint(blueprint)
