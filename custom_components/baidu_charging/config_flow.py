import logging
import aiohttp
import json
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from . import (
    DOMAIN,
    StateCoordinator, callback, cv,
    TITLE, DEFAULT_INTERVAL,
    CONF_NAME, CONF_API_KEY, CONF_POI_UID, CONF_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
CONF_REGION = 'region'
CONF_SEARCH = 'search'


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(entry)

    async def async_step_user(self, user_input=None):
        self.hass.data.setdefault(DOMAIN, {})
        if user_input is None:
            user_input = {}
        schema = {}
        errors = {}
        apikey = user_input.get(CONF_API_KEY) or ''
        search = user_input.get(CONF_SEARCH)
        region = user_input.get(CONF_REGION)
        poi_uid = user_input.get(CONF_POI_UID, '')

        if poi_uid:
            pass

        elif search and apikey:
            msg = ''
            api = 'https://api.map.baidu.com/place/v2/search'
            try:
                res = await async_get_clientsession(self.hass).get(
                    url=api,
                    params={
                        'ak': apikey,
                        'region': region,
                        'query': f'{search}充电站',
                        'output': 'json',
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                data = json.loads(await res.text() or '{}')
                results = {
                    v.get('uid'): v.get('name')
                    for v in data.get('results') or []
                }
                if not results:
                    msg = data.get('message', '')
            except Exception as err:
                results = {}
                msg = str(err)
                _LOGGER.error('Request %s error: %s', api, err)
            if results:
                if poi_uid not in results:
                    poi_uid = ''
                results = {
                    '': '重新搜索',
                    **results,
                }
                schema.update({
                    vol.Optional(CONF_POI_UID, default=poi_uid): vol.In(results),
                })
            else:
                self.context['tip'] = f'未找到与【{search}】相关的结果\n{msg}'

        elif search:
            if '/j.map.baidu.com/' in search:
                res = await async_get_clientsession(self.hass).get(search.strip(), allow_redirects=False)
                if url := res.headers.get(aiohttp.hdrs.LOCATION):
                    search = url
                else:
                    _LOGGER.warning('async_step_user %s', [url, res.headers, user_input])
            if fls := re.findall(r'uid(?:%3D|=)(\w{16,})', search):
                poi_uid = fls[0]
            else:
                self.context['tip'] = f'分享链接不正确\n{search}'

        if poi_uid:
            result = await StateCoordinator.async_get_station(self.hass, poi_uid)
            data = result.get('data') or {}
            basic_info = data.get('basic_info')
            if not basic_info:
                self.context['tip'] = f'找不到该地址的充电站信息\n{result}'
            elif not to_time_period(user_input.get(CONF_SCAN_INTERVAL)):
                self.context['tip'] = '⚠️ 更新频率格式错误'
            else:
                await self.async_set_unique_id(poi_uid)
                self._abort_if_unique_id_configured()
                user_input.pop(CONF_SEARCH, None)
                user_input[CONF_POI_UID] = poi_uid
                user_input[CONF_NAME] = basic_info.get('name', '')
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or TITLE,
                    data=user_input,
                )

        if not self.context.get('tip'):
            self.context['tip'] = '使用百度地图APP搜索充电站，并获取分享链接'

        latest_apikey = self.hass.data[DOMAIN].get('latest_apikey')
        schema = {
            vol.Required(CONF_SEARCH, default=user_input.get(CONF_SEARCH)): str,
            # vol.Optional(CONF_REGION, default=user_input.get(CONF_REGION)): str,
            # vol.Optional(CONF_API_KEY, default=user_input.get(CONF_API_KEY, latest_apikey)): str,
            **schema,
            vol.Optional(CONF_SCAN_INTERVAL, default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_INTERVAL)): str,
        }
        return self.async_show_form(
            step_id='user',
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={'tip': self.context.pop('tip', '')},
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is None:
            user_input = {}
        if user_input:
            if not to_time_period(user_input.get(CONF_SCAN_INTERVAL)):
                self.context['tip'] = '⚠️ 更新频率格式错误'
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, **user_input}
                )
                return self.async_create_entry(title='', data={})

        defaults = {
            **self.config_entry.data,
            **self.config_entry.options,
            **user_input,
        }
        if not self.context.get('tip'):
            self.context['tip'] = '如需修改充电站，请重新添加集成'
        return self.async_show_form(
            step_id='init',
            data_schema=vol.Schema({
                vol.Optional(CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_INTERVAL)): str,
            }),
            description_placeholders={'tip': self.context.pop('tip', '')},
        )

def to_time_period(val):
    try:
        val = cv.time_period(val or DEFAULT_INTERVAL)
    except Exception:
        val = None
    return val