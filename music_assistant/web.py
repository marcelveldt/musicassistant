#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import asyncio
import os
import aiohttp
from aiohttp import web
from functools import partial
import ssl
import concurrent
import threading
from .models.media_types import MediaItem, MediaType, media_type_from_string
from .models.player import Player
from .utils import run_periodic, LOGGER, run_async_background_task, get_ip, json_serializer

CONF_KEY = 'web'
CONFIG_ENTRIES = [
        ('http_port', 8095, 'web_http_port'),
        ('https_port', 8096, 'web_https_port'),
        ('ssl_certificate', '', 'web_ssl_cert'), 
        ('ssl_key', '', 'web_ssl_key'),
        ('cert_fqdn_host', '', 'cert_fqdn_host')
        ]

class Web():
    ''' webserver and json/websocket api '''
    
    def __init__(self, mass):
        self.mass = mass
        # load/create/update config
        config = self.mass.config.create_module_config(CONF_KEY, CONFIG_ENTRIES)
        if config['ssl_certificate'] and not os.path.isfile(
                config['ssl_certificate']):
            raise FileNotFoundError(
                "SSL certificate file not found: %s" % config['ssl_certificate'])
        if config['ssl_key'] and not os.path.isfile(config['ssl_key']):
            raise FileNotFoundError(
                "SSL certificate key file not found: %s" % config['ssl_key'])
        self.local_ip = get_ip()
        self.http_port = config['http_port']
        self.https_port = config['https_port']
        self._enable_ssl = config['ssl_certificate'] and config['ssl_key']
        self.config = config

    async def setup(self):
        ''' perform async setup '''
        self.http_session = aiohttp.ClientSession()
        app = web.Application()
        app.add_routes([web.get('/jsonrpc.js', self.json_rpc)])
        app.add_routes([web.post('/jsonrpc.js', self.json_rpc)])
        app.add_routes([web.get('/ws', self.websocket_handler)])
        app.add_routes([web.get('/stream/{player_id}', self.mass.http_streamer.stream)])
        app.add_routes([web.get('/stream/web/{player_id}', self.mass.http_streamer.webplayer)])
        app.add_routes([web.get('/stream/{player_id}/{queue_item_id}', self.mass.http_streamer.stream)])
        app.add_routes([web.get('/api/search', self.search)])
        app.add_routes([web.get('/api/config', self.get_config)])
        app.add_routes([web.post('/api/config/{key}/{subkey}', self.save_config)])
        app.add_routes([web.get('/api/players', self.players)])
        app.add_routes([web.get('/api/players/{player_id}', self.player)])
        app.add_routes([web.get('/api/players/{player_id}/queue', self.player_queue)])
        app.add_routes([web.get('/api/players/{player_id}/cmd/{cmd}', self.player_command)])
        app.add_routes([web.get('/api/players/{player_id}/cmd/{cmd}/{cmd_args}', self.player_command)])
        app.add_routes([web.get('/api/players/{player_id}/play_media/{media_type}/{media_id}', self.play_media)])
        app.add_routes([web.get('/api/players/{player_id}/play_media/{media_type}/{media_id}/{queue_opt}', self.play_media)])
        app.add_routes([web.get('/api/playlists/{playlist_id}/tracks', self.playlist_tracks)])
        app.add_routes([web.get('/api/artists/{artist_id}/toptracks', self.artist_toptracks)])
        app.add_routes([web.get('/api/artists/{artist_id}/albums', self.artist_albums)])
        app.add_routes([web.get('/api/albums/{album_id}/tracks', self.album_tracks)])
        app.add_routes([web.get('/api/{media_type}', self.get_items)])
        app.add_routes([web.get('/api/{media_type}/{media_id}/{action}', self.get_item)])
        app.add_routes([web.get('/api/{media_type}/{media_id}', self.get_item)])
        app.add_routes([web.get('/', self.index)])
        webdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web/')
        app.router.add_static("/", webdir)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        http_site = web.TCPSite(self.runner, '0.0.0.0', self.http_port)
        await http_site.start()
        if self._enable_ssl:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(self.config['ssl_certificate'], self.config['ssl_key'])
            https_site = web.TCPSite(self.runner, '0.0.0.0', self.https_port, ssl_context=ssl_context)
            await https_site.start()

    async def get_items(self, request):
        ''' get multiple library items'''
        media_type_str = request.match_info.get('media_type')
        media_type = media_type_from_string(media_type_str)
        limit = int(request.query.get('limit', 50))
        offset = int(request.query.get('offset', 0))
        orderby = request.query.get('orderby', 'name')
        provider_filter = request.rel_url.query.get('provider')
        result = await self.mass.music.library_items(media_type, 
                    limit=limit, offset=offset, 
                    orderby=orderby, provider_filter=provider_filter)
        return web.json_response(result, dumps=json_serializer)

    async def get_item(self, request):
        ''' get item full details'''
        media_type_str = request.match_info.get('media_type')
        media_type = media_type_from_string(media_type_str)
        media_id = request.match_info.get('media_id')
        action = request.match_info.get('action','')
        action_details = request.rel_url.query.get('action_details')
        lazy = request.rel_url.query.get('lazy', '') != 'false'
        provider = request.rel_url.query.get('provider')
        if action:
            result = await self.mass.music.item_action(media_id, media_type, provider, action, action_details)
        else:
            result = await self.mass.music.item(media_id, media_type, provider, lazy=lazy)
        return web.json_response(result, dumps=json_serializer)

    async def artist_toptracks(self, request):
        ''' get top tracks for given artist '''
        artist_id = request.match_info.get('artist_id')
        provider = request.rel_url.query.get('provider')
        result = await self.mass.music.artist_toptracks(artist_id, provider)
        return web.json_response(result, dumps=json_serializer)

    async def artist_albums(self, request):
        ''' get (all) albums for given artist '''
        artist_id = request.match_info.get('artist_id')
        provider = request.rel_url.query.get('provider')
        result = await self.mass.music.artist_albums(artist_id, provider)
        return web.json_response(result, dumps=json_serializer)

    async def playlist_tracks(self, request):
        ''' get playlist tracks from provider'''
        playlist_id = request.match_info.get('playlist_id')
        limit = int(request.query.get('limit', 50))
        offset = int(request.query.get('offset', 0))
        provider = request.rel_url.query.get('provider')
        result = await self.mass.music.playlist_tracks(playlist_id, provider, offset=offset, limit=limit)
        return web.json_response(result, dumps=json_serializer)

    async def album_tracks(self, request):
        ''' get album tracks from provider'''
        album_id = request.match_info.get('album_id')
        provider = request.rel_url.query.get('provider')
        result = await self.mass.music.album_tracks(album_id, provider)
        return web.json_response(result, dumps=json_serializer)

    async def search(self, request):
        ''' search database or providers '''
        searchquery = request.rel_url.query.get('query')
        media_types_query = request.rel_url.query.get('media_types')
        limit = request.rel_url.query.get('media_id', 5)
        online = request.rel_url.query.get('online', False)
        media_types = []
        if not media_types_query or "artists" in media_types_query:
            media_types.append(MediaType.Artist)
        if not media_types_query or "albums" in media_types_query:
            media_types.append(MediaType.Album)
        if not media_types_query or "tracks" in media_types_query:
            media_types.append(MediaType.Track)
        if not media_types_query or "playlists" in media_types_query:
            media_types.append(MediaType.Playlist)
        if not media_types_query or "radios" in media_types_query:
            media_types.append(MediaType.Radio)
        # get results from database
        result = await self.mass.music.search(searchquery, media_types, limit=limit, online=online)
        return web.json_response(result, dumps=json_serializer)

    async def players(self, request):
        ''' get all players '''
        players = list(self.mass.players.players)
        players.sort(key=lambda x: x.name, reverse=False)
        return web.json_response(players, dumps=json_serializer)

    async def player(self, request):
        ''' get single player '''
        player_id = request.match_info.get('player_id')
        player = await self.mass.players.get_player(player_id)
        return web.json_response(player, dumps=json_serializer)

    async def player_command(self, request):
        ''' issue player command'''
        result = False
        player_id = request.match_info.get('player_id')
        player = await self.mass.players.get_player(player_id)
        if player:
            cmd = request.match_info.get('cmd')
            cmd_args = request.match_info.get('cmd_args')
            player_cmd = getattr(player, cmd, None)
            if player_cmd and cmd_args != None:
                result = await player_cmd(cmd_args)
            elif player_cmd:
                result = await player_cmd()
            else:
                LOGGER.error("Received non-existing command %s for player %s" %(cmd, player.name))
        else:
            LOGGER.error("Received command for non-existing player %s" %(player_id))
        return web.json_response(result, dumps=json_serializer) 
    
    async def play_media(self, request):
        ''' issue player play_media command'''
        player_id = request.match_info.get('player_id')
        media_type_str = request.match_info.get('media_type')
        media_type = media_type_from_string(media_type_str)
        media_id = request.match_info.get('media_id')
        queue_opt = request.match_info.get('queue_opt','')
        provider = request.rel_url.query.get('provider')
        media_item = await self.mass.music.item(media_id, media_type, provider, lazy=True)
        result = await self.mass.players.play_media(player_id, media_item, queue_opt)
        return web.json_response(result, dumps=json_serializer) 
    
    async def player_queue(self, request):
        ''' return the items in the player's queue '''
        player_id = request.match_info.get('player_id')
        limit = int(request.query.get('limit', 50))
        offset = int(request.query.get('offset', 0))
        player = await self.mass.players.get_player(player_id)
        # queue_items = player.queue.items
        # queue_items = [item.__dict__ for item in queue_items]
        # print(queue_items)
        # result = queue_items[offset:limit]
        return web.json_response(player.queue.items[offset:limit], dumps=json_serializer) 
    
    async def index(self, request):
        index_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'web/index.html')
        return web.FileResponse(index_file)

    async def websocket_handler(self, request):
        ''' websockets handler '''
        cb_id = None
        ws = None
        try:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            # register callback for internal events
            async def send_event(msg, msg_details):
                ws_msg = {"message": msg, "message_details": msg_details }
                await ws.send_json(ws_msg, dumps=json_serializer)
            cb_id = await self.mass.add_event_listener(send_event)
            # process incoming messages
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                # for now we only use WS for (simple) player commands
                if msg.data == 'players':
                    players = list(self.mass.players.players)
                    players.sort(key=lambda x: x.name, reverse=False)
                    ws_msg = {'message': 'players', 'message_details': players}
                    await ws.send_json(ws_msg, dumps=json_serializer)
                elif msg.data.startswith('players') and '/cmd/' in msg.data:
                    # players/{player_id}/cmd/{cmd} or players/{player_id}/cmd/{cmd}/{cmd_args}
                    msg_data_parts = msg.data.split('/')
                    player_id = msg_data_parts[1]
                    cmd = msg_data_parts[3]
                    cmd_args = msg_data_parts[4] if len(msg_data_parts) == 5 else None
                    player = await self.mass.players.get_player(player_id)
                    player_cmd = getattr(player, cmd, None)
                    if player_cmd and cmd_args:
                        result = await player_cmd(cmd_args)
                    elif player_cmd:
                        result = await player_cmd()
        finally:
            await self.mass.remove_event_listener(cb_id)
        LOGGER.debug('websocket connection closed')
        return ws

    async def get_config(self, request):
        ''' get the config '''
        return web.json_response(self.mass.config)

    async def save_config(self, request):
        ''' save (partial) config '''
        conf_key = request.match_info.get('key')
        conf_subkey = request.match_info.get('subkey')
        new_values = await request.json()
        LOGGER.debug(f'save config called for {conf_key}/{conf_subkey} - new value: {new_values}')
        cur_values = self.mass.config[conf_key][conf_subkey]
        result = {"success": True, "restart_required": False, "settings_changed": False}
        if cur_values != new_values:
            # config changed
            result["settings_changed"] = True
            self.mass.config[conf_key][conf_subkey] = new_values
            if conf_key != "player_settings":
                result["restart_required"] = True
            self.mass.config.save()
        return web.json_response(result)

    async def json_rpc(self, request):
        ''' 
            implement LMS jsonrpc interface 
            for some compatability with tools that talk to lms
            only support for basic commands
        '''
        data = await request.json()
        LOGGER.debug("jsonrpc: %s" % data)
        params = data['params']
        player_id = params[0]
        cmds = params[1]
        cmd_str = " ".join(cmds)
        player = await self.mass.players.get_player(player_id)
        if cmd_str == 'play':
            await player.play()
        elif cmd_str == 'pause':
            await player.pause()
        elif cmd_str == 'stop':
            await player.stop()
        elif cmd_str == 'next':
            await player.next()
        elif cmd_str == 'previous':
            await player.previous()
        elif 'power' in cmd_str:
            args = cmds[1] if len(cmds) > 1 else None
            await player.power(args)
        elif cmd_str == 'playlist index +1':
            await player.next()
        elif cmd_str == 'playlist index -1':
            await player.previous()
        elif 'mixer volume' in cmd_str:
            await player.volume_set(cmds[2])
        elif cmd_str == 'mixer muting 1':
            await player.volume_mute(True)
        elif cmd_str == 'mixer muting 0':
            await player.volume_mute(False)
        elif cmd_str == 'button volup':
            await player.volume_up()
        elif cmd_str == 'button voldown':
            await player.volume_down()
        elif cmd_str == 'button power':
            await player.power_toggle()
        else:
            return web.Response(text='command not supported')
        return web.Response(text='success')
        