# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------


import os
import json
import jwt
import platform
import subprocess
import datetime
from azure.cli.core._profile import Profile, _CLIENT_ID
from six.moves.urllib.parse import urlparse


STORAGE_RESOURCE_ENDPOINT = "https://storage.azure.com"
SERVICES = {'blob', 'file'}
AZCOPY_VERSION = '10.0.5'

class AzCopy(object):
    system_executable_path = {
        'Darwin': ['azcopy_darwin_amd64_{}'.format(AZCOPY_VERSION), 'azcopy'],
        'Linux': ['azcopy_linux_amd64_{}'.format(AZCOPY_VERSION), 'azcopy'],
        'Windows': ['azcopy_windows_amd64_{}'.format(AZCOPY_VERSION), 'azcopy.exe']
    }

    def __init__(self, creds=None):
        self.system = platform.system()
        curr_path = os.path.dirname(os.path.realpath(__file__))
        self.executable = os.path.join(curr_path, *AzCopy.system_executable_path[self.system])
        self.creds = creds

    def run_command(self, args):
        args = [self.executable] + args
        env_kwargs = {}
        if self.creds and self.creds.token_info:
            print(repr(json.dumps(self.creds.token_info)), type(json.dumps(self.creds.token_info)))
            env_kwargs = {'AZCOPY_OAUTH_TOKEN_INFO': json.dumps(self.creds.token_info)}
        subprocess.call(args, env=dict(os.environ, **env_kwargs))

    def copy(self, source, destination, flags=None):
        flags = flags or []
        self.run_command(['copy', source, destination] + flags)


class AzCopyCredentials(object):
    def __init__(self, sas_token=None, token_info=None):
        self.sas_token = sas_token
        self.token_info = token_info


def blob_client_auth_for_azcopy(cmd, blob_client):
    azcopy_creds = storage_client_auth_for_azcopy(cmd, blob_client, 'blob')
    if azcopy_creds is not None:
        return azcopy_creds

    # oauth mode
    token_info = Profile(cli_ctx=cmd.cli_ctx).get_raw_token(resource=STORAGE_RESOURCE_ENDPOINT)[0][2]
    try:
        token_info = _unserialize_non_msi_token_payload(token_info)
    except KeyError:  # unserialized MSI token payload
        raise Exception('MSI auth not yet supported.')
    return AzCopyCredentials(token_info=token_info)


def storage_client_auth_for_azcopy(cmd, client, service):
    if service not in SERVICES:
        raise Exception('{} not one of: {}'.format(service, str(SERVICES)))

    if client.sas_token:
        return AzCopyCredentials(sas_token=client.sas_token)

    # if account key provided, generate a sas token
    if client.account_key:
        sas_token = _generate_sas_token(cmd, client.account_name, client.account_key, service)
        return AzCopyCredentials(sas_token=sas_token)
    return None


def _unserialize_non_msi_token_payload(token_info):
    parsed_authority = urlparse(token_info['_authority'])
    decode = jwt.decode(token_info['accessToken'], verify=False, algorithms=['RS256'])
    return {
        'access_token': token_info['accessToken'],
        'refresh_token': token_info['refreshToken'],
        'expires_in': str(token_info['expiresIn']),
        'not_before': str(decode['nbf']),
        'expires_on': str(int((datetime.datetime.strptime(
            token_info['expiresOn'], "%Y-%m-%d %H:%M:%S.%f")).timestamp())),
        'resource': STORAGE_RESOURCE_ENDPOINT,
        'token_type': token_info['tokenType'],
        '_tenant': parsed_authority.path.strip('/'),
        '_client_id': token_info['_clientId'],
        '_ad_endpoint': '{uri.scheme}://{uri.netloc}'.format(uri=parsed_authority)
    }


def _generate_sas_token(cmd, account_name, account_key, service):
    from .._client_factory import cloud_storage_account_service_factory
    from .._validators import resource_type_type, services_type

    kwargs = {
        'account_name': account_name,
        'account_key': account_key
    }
    cloud_storage_client = cloud_storage_account_service_factory(cmd.cli_ctx, kwargs)
    t_account_permissions = cmd.loader.get_sdk('common.models#AccountPermissions')

    return cloud_storage_client.generate_shared_access_signature(
        services_type(cmd.loader)(service[0]),
        resource_type_type(cmd.loader)('sco'),
        t_account_permissions(_str='rwdlacup'),
        datetime.datetime.utcnow() + datetime.timedelta(days=1)
    )
