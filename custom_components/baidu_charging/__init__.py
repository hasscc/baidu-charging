import logging
import aiohttp
import voluptuous as vol

from datetime import timedelta
from homeassistant.core import HomeAssistant, State, ServiceCall, SupportsResponse, callback
from homeassistant.const import (
    Platform,
    CONF_NAME,
    CONF_API_KEY,
    STATE_IDLE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .converters.base import *

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'baidu_charging'
TITLE = '充电站'
API_BASE = 'https://charging.map.baidu.com/charge_service'
CONF_POI_UID = 'poi_uid'
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0'

SUPPORTED_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, hass_config):

    async def update_status(call: ServiceCall):
        uid = call.data.get(CONF_POI_UID) or call.data.get('uid')
        entries = hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if uid and uid != entry.unique_id:
                continue
            coordinator = hass.data.get(entry.entry_id, {}).get('coordinator')
            if not coordinator:
                continue
            return await coordinator.async_update_station(uid)
        if uid:
            return await StateCoordinator.async_get_station(hass, uid)
        return {'error': 'Entry not found'}
    hass.services.async_register(
        DOMAIN, 'update_status', update_status,
        schema=vol.Schema({}, extra=vol.ALLOW_EXTRA),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    hass.data.setdefault(entry.entry_id, {})
    hass.data[entry.entry_id].setdefault('entities', {})
    coordinator = StateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[entry.entry_id]['coordinator'] = coordinator
    hass.data[DOMAIN]['latest_apikey'] = coordinator.data.get(CONF_API_KEY)

    await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)

    return True


class StateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=f'{entry.entry_id}-coordinator',
            update_interval=timedelta(seconds=60),
        )
        self.data = {}
        self.entry = entry
        self.stations = {}
        self.entities = {}
        self.converters = {}

        from homeassistant.components.sensor import SensorStateClass
        self.add_converters(*[
            NumberSensorConv('total_left', precision=0).with_option({
                'icon': 'mdi:ev-station',
                'state_class': SensorStateClass.MEASUREMENT,
            }),
            SensorConv('dc_left', prop='charge_connector_stat.dc_left', parent='total_left'),
            SensorConv('ac_left', prop='charge_connector_stat.ac_left', parent='total_left'),
            SensorConv('dc_total', prop='charge_connector_stat.dc_total', parent='total_left'),
            SensorConv('dc_off', prop='charge_connector_stat.dc_off', parent='total_left'),
            SensorConv('dc_fault', prop='charge_connector_stat.dc_fault', parent='total_left'),
            SensorConv('dc_occu', prop='charge_connector_stat.dc_occu', parent='total_left'),
            SensorConv('dc_min_power', prop='charge_connector_stat.dc_min_power', parent='total_left'),
            SensorConv('dc_max_power', prop='charge_connector_stat.dc_max_pwer', parent='total_left'),
            SensorConv('dc_power_text', prop='charge_connector_stat.dc_power_text', parent='total_left'),
            SensorConv('dc_idle_predict', prop='charge_connector_stat.dc_idle_predict', parent='total_left'),
            SensorConv('ac_total', prop='charge_connector_stat.ac_total', parent='total_left'),
            SensorConv('ac_off', prop='charge_connector_stat.ac_off', parent='total_left'),
            SensorConv('ac_fault', prop='charge_connector_stat.ac_fault', parent='total_left'),
            SensorConv('ac_occu', prop='charge_connector_stat.ac_occu', parent='total_left'),
            SensorConv('ac_min_power', prop='charge_connector_stat.ac_min_power', parent='total_left'),
            SensorConv('ac_max_power', prop='charge_connector_stat.ac_max_power', parent='total_left'),
            SensorConv('ac_power_text', prop='charge_connector_stat.ac_power_text', parent='total_left'),
            SensorConv('ac_idle_predict', prop='charge_connector_stat.ac_idle_predict', parent='total_left'),

            SensorConv('park_info', prop='additional_info.park_current_info').with_option({
                'icon': 'mdi:parking',
            }),
            SensorConv('uid', prop='basic_info.uid', parent='park_info'),
            SensorConv('addr', prop='basic_info.addr', parent='park_info'),
            SensorConv('park_detail', prop='charge_connector_stat.park_info', parent='park_info'),
            SensorConv('park_extend', prop='charge_connector_stat.park_extend', parent='park_info'),
        ])

    def add_converter(self, conv: Converter):
        self.converters[conv.attr] = conv

    def add_converters(self, *args: Converter):
        for conv in args:
            self.add_converter(conv)

    @property
    def poi_uid(self):
        return self.entry.data.get(CONF_POI_UID, '')

    @property
    def api_key(self):
        return self.entry.data.get(CONF_API_KEY, '')

    @property
    def entity_prefix(self):
        uid = f'{self.poi_uid}'.lower()
        return f'{uid[:4]}_{uid[-4:]}'

    @property
    def basic_info(self):
        return self.data.get('basic_info') or {}

    @property
    def station_name(self):
        return self.entry.data.get(CONF_NAME, '') or self.basic_info.get('name', '')

    @property
    def addr(self):
        return self.basic_info.get('addr', '')

    async def _async_update_data(self):
        await self.async_update_station()
        return self.data

    async def async_update_station(self, uid=None):
        if not uid:
            uid = self.poi_uid
        result = await StateCoordinator.async_get_station(self.hass, uid)
        data = result.get('data') or {}
        stat = data.get('charge_connector_stat') or {}
        data.update({
            'total_left': stat.get('dc_left', 0) + stat.get('ac_left', 0),
        })

        idx = -1
        for dat in data.get('tp_list', []):
            idx += 1
            if not (station_id := dat.get('tp_id')):
                continue
            fees = dat.get('current_charge_fee') or {}
            dat['total_price'] = fees.get('MarketElecPrice', 0) + fees.get('MarketServicePrice', 0)
            station = self.stations.get(station_id)
            if not isinstance(station, ChargingStation):
                station = ChargingStation(self, dat, idx)
                self.stations[station_id] = station
            await station.async_update_connectors(dat)

        self.data.update(data)
        return data

    @staticmethod
    async def async_get_station(hass, uid):
        return await StateCoordinator.async_request(hass, 'charge_station/get_charge_detail', params={
            'uid': uid,
        })

    @staticmethod
    async def async_request(hass, api: str, **kwargs):
        kwargs.setdefault('method', 'GET')
        kwargs.setdefault('url', f'{API_BASE}/{api.lstrip("/")}')
        kwargs['params'] = {
            'sv': '19.0.0',
            'os': 'ios',
            'cuid': '',
            'callback': '',
            **kwargs.get('params', {}),
        }
        kwargs['headers'] = {
            aiohttp.hdrs.CONTENT_TYPE: 'application/json; charset=UTF-8',
            aiohttp.hdrs.USER_AGENT: USER_AGENT,
            **kwargs.get('headers', {}),
        }
        try:
            res = await async_get_clientsession(hass).request(
                **kwargs,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        except Exception as err:
            _LOGGER.error('Request %s error: %s', api, err)
            return {}
        result = await res.json() or {}
        logger = _LOGGER.info if result.get('data') else _LOGGER.warning
        logger('Request %s result: %s', api, [result, kwargs])
        return result

    def decode(self, data: dict) -> dict:
        """Decode props for HASS."""
        payload = {}
        for conv in self.converters.values():
            prop = conv.prop or conv.attr
            value = get_value(data, prop, None)
            if prop is None:
                continue
            conv.decode(self, payload, value)
        return payload

    def push_state(self, value: dict):
        """Push new state to Hass entities."""
        if not value:
            return
        attrs = value.keys()

        for entity in self.entities.values():
            if not hasattr(entity, 'subscribed_attrs'):
                continue
            if not (entity.subscribed_attrs & attrs):
                continue
            entity.async_set_state(value)
            if entity.added:
                entity.async_write_ha_state()

    def subscribe_attrs(self, conv: Converter):
        attrs = {conv.attr}
        if conv.childs:
            attrs |= set(conv.childs)
        attrs.update(c.attr for c in self.converters.values() if c.parent == conv.attr)
        return attrs

class ChargingStation:
    def __init__(self, coordinator: StateCoordinator, data: dict, idx=0):
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.data = data

        from homeassistant.components.sensor import SensorDeviceClass
        coordinator.add_converters(*[
            SensorConv('price', prop=f'tp_list.{idx}.total_price').with_option({
                'device_class': SensorDeviceClass.MONETARY,
                'unit_of_measurement': 'CNY',
            }),
            Converter('time_period', prop=f'tp_list.{idx}.current_charge_fee.Time', parent='price'),
            Converter('electric_price', prop=f'tp_list.{idx}.current_charge_fee.MarketElecPrice', parent='price'),
            Converter('service_price', prop=f'tp_list.{idx}.current_charge_fee.MarketServicePrice', parent='price'),
            Converter('hundred_km_charge_fee', prop=f'tp_list.{idx}.hundred_km_charge_fee', parent='price'),
            Converter('list', prop=f'tp_list.{idx}.cf', parent='price'),
        ])

    @property
    def station_id(self):
        return self.data.get('tp_id', '')

    @property
    def tp_code(self):
        return self.data.get('tp_code', 88)

    async def async_update_connectors(self, station_data=None):
        result = await self.coordinator.async_request(self.hass, 'charge_station/get_connector_detail', params={
            'uid': self.coordinator.poi_uid,
            'station_id': self.station_id,
            'tp_code': self.tp_code,
        })
        data = result.get('data') or {}

        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        self.coordinator.data.setdefault('connectors', {})
        for lst in [data.get('fast') or [], data.get('slow') or []]:
            for conn in lst:
                cid = conn.get('connector_id')
                connectors = self.coordinator.data['connectors']
                if cid in connectors:
                    connectors[cid] = conn
                    continue
                connectors[cid] = conn
                attr = f'connector_{cid[-6:]}'
                self.coordinator.add_converters(*[
                    MapSensorConv(attr, prop=f'connectors.{cid}.status', map={
                        0: 'fault',
                        1: STATE_IDLE,
                        2: 'occupied',
                    }).with_option({
                        'name': conn.get('connector_name'),
                        'icon': 'mdi:power-plug',
                        'device_class': BinarySensorDeviceClass.PLUG,
                        'translation_key': 'connector_status',
                    }),
                    Converter('connector_name', prop=f'connectors.{cid}.connector_name', parent=attr),
                    Converter('power', prop=f'connectors.{cid}.power', parent=attr),
                    Converter('can_down_lock', prop=f'connectors.{cid}.can_down_lock', parent=attr),
                    Converter('lock_title', prop=f'connectors.{cid}.lock_title', parent=attr),
                ])

        if station_data:
            self.data.update(station_data)
        self.data.update(data)
        return data


class XEntity(CoordinatorEntity):
    log = _LOGGER
    added = False
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: StateCoordinator, conv: Converter, option=None):
        super().__init__(coordinator)
        self.conv = conv
        self.attr = conv.attr
        self.hass = coordinator.hass
        self.entry = coordinator.entry
        self._option = option or {}
        if hasattr(conv, 'option'):
            self._option.update(conv.option or {})
        self.entity_id = f'{conv.domain}.{coordinator.entity_prefix}_{conv.attr}'
        self._attr_unique_id = f'{DOMAIN}-{self.entry.entry_id}-{coordinator.poi_uid}-{self.attr}'
        if name := self._option.get('name'):
            self._attr_name = name
        self._attr_icon = self._option.get('icon')
        self._attr_device_class = self._option.get('device_class')
        self._attr_entity_picture = self._option.get('entity_picture')
        self._attr_entity_category = self._option.get('entity_category')
        self._attr_translation_key = self._option.get('translation_key', conv.attr)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.poi_uid)},
            name=coordinator.station_name,
            model='\n\n'.join([
                coordinator.station_name,
                coordinator.addr,
                coordinator.poi_uid,
            ]),
            configuration_url=f'https://map.baidu.com/?newmap=1&s=inf%26uid%3D{coordinator.poi_uid}',
        )
        self._attr_entity_registry_enabled_default = conv.enabled is not False
        self._attr_extra_state_attributes = {}
        self._vars = {}
        self.subscribed_attrs = coordinator.subscribe_attrs(conv)
        coordinator.entities[conv.attr] = self

    @property
    def vin(self):
        return self.coordinator.vin

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        if hasattr(self, 'async_get_last_state'):
            state: State = await self.async_get_last_state()
            if state:
                self.async_restore_last_state(state.state, state.attributes)

        self.added = True
        self.update()

    @callback
    def async_restore_last_state(self, state: str, attrs: dict):
        """Restore previous state."""
        self._attr_state = state

    @callback
    def async_set_state(self, data: dict):
        """Handle state update from gateway."""
        if hasattr(self.conv, 'option'):
            self._option.update(self.conv.option or {})
        if self.attr in data:
            self._attr_state = data[self.attr]
            self._attr_entity_picture = self._option.get('entity_picture')
        for k in self.subscribed_attrs:
            if k not in data:
                continue
            self._attr_extra_state_attributes[k] = data[k]
        _LOGGER.info('%s: State changed: %s', self.entity_id, data)

    def update(self):
        payload = self.coordinator.decode(self.coordinator.data)
        self.coordinator.push_state(payload)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update()
