from aiohttp import web

async def handle(request):
    hits = request.app['hits']
    path = request.path
    hits[path] = hits.get(path, 0) + 1
    return web.json_response({'path': path})

async def create_mock_server():
    app = web.Application()
    app['hits'] = {}
    app.router.add_get('/ping', handle)
    app.router.add_get('/ping2', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f'http://localhost:{port}'
    return runner, base_url, app['hits']

async def shutdown_mock_server(runner):
    await runner.cleanup()
