"""Small HTTPS tracker drivers with a common issue result contract."""
import base64
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _segment(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,199}', value) or '..' in value:
        raise ValueError('Invalid tracker path segment')
    return quote(value, safe='')


def _request(url, headers, method='GET', payload=None, timeout=30):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Tracker endpoints require HTTPS without URL credentials')
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers=headers, method=method)
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('Tracker response exceeds size budget')
        return json.loads(body) if body else None


def invoke(provider, operation, params, timeout=30):
    if operation not in ('read', 'update-title'):
        raise ValueError('Unsupported builtin tracker capability')
    identity = _segment(params['id'])
    if operation == 'update-title' and (not isinstance(params.get('title'), str) or not params['title'].strip()):
        raise ValueError('Title must be a nonempty string')
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'skillsync'}
    if provider == 'github':
        repo = params['repo'].split('/')
        if len(repo) != 2 or not str(params['id']).isdigit():
            raise ValueError('GitHub requires owner/repo and a numeric issue id')
        url = 'https://api.github.com/repos/' + '/'.join(_segment(part) for part in repo) + '/issues/' + identity
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        headers['Accept'] = 'application/vnd.github+json'
        if operation == 'update-title':
            _request(url, headers, 'PATCH', {'title': params['title']}, timeout)
        data = _request(url, headers, timeout=timeout)
        return {'provider': provider, 'id': str(data['number']), 'title': data['title'], 'state': data['state'], 'url': data['html_url']}
    if provider == 'jira':
        endpoint = os.environ.get('JIRA_URL', '').rstrip('/')
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Configure a credential-free HTTPS JIRA_URL')
        token = os.environ.get('JIRA_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        elif os.environ.get('JIRA_USER') and os.environ.get('JIRA_API_TOKEN'):
            pair = os.environ['JIRA_USER'] + ':' + os.environ['JIRA_API_TOKEN']
            headers['Authorization'] = 'Basic ' + base64.b64encode(pair.encode()).decode()
        else:
            raise ValueError('Configure Jira authentication in environment')
        url = endpoint + '/rest/api/2/issue/' + identity
        if operation == 'update-title':
            _request(url, headers, 'PUT', {'fields': {'summary': params['title']}}, timeout)
        data = _request(url, headers, timeout=timeout)
        return {'provider': provider, 'id': data['key'], 'title': data['fields']['summary'], 'state': data['fields']['status']['name'], 'url': endpoint + '/browse/' + _segment(data['key'])}
    if provider == 'azure':
        if not str(params['id']).isdigit():
            raise ValueError('Azure requires a numeric work item id')
        token = os.environ.get('AZURE_DEVOPS_EXT_PAT')
        if not token:
            raise ValueError('Configure AZURE_DEVOPS_EXT_PAT in environment')
        headers['Authorization'] = 'Basic ' + base64.b64encode((':' + token).encode()).decode()
        url = 'https://dev.azure.com/' + _segment(params['organization']) + '/' + _segment(params['project']) + '/_apis/wit/workitems/' + identity + '?api-version=7.1'
        if operation == 'update-title':
            patch_headers = dict(headers, **{'Content-Type': 'application/json-patch+json'})
            _request(url, patch_headers, 'PATCH', [{'op': 'add', 'path': '/fields/System.Title', 'value': params['title']}], timeout)
        data = _request(url, headers, timeout=timeout)
        return {'provider': provider, 'id': str(data['id']), 'title': data['fields']['System.Title'], 'state': data['fields']['System.State'], 'url': data['_links']['html']['href']}
    raise ValueError('Unsupported builtin tracker provider')
