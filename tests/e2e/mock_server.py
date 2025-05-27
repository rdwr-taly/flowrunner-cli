from aiohttp import web

async def handle(request: web.Request) -> web.Response:
    hits = request.app['hits']
    requests = request.app['requests']
    path = request.path
    hits[path] = hits.get(path, 0) + 1
    body = await request.text()
    requests.append({'path': path, 'method': request.method, 'headers': dict(request.headers), 'body': body})
    return web.json_response({'path': path})

async def create_mock_server():
    app = web.Application()
    app['hits'] = {}
    app['requests'] = []
    app.router.add_get('/ping', handle)
    app.router.add_get('/ping2', handle)
    app.router.add_post('/echo', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f'http://127.0.0.1:{port}'
    return runner, base_url, app['hits'], app['requests']

async def shutdown_mock_server(runner):
    await runner.cleanup()
