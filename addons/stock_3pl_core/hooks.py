def post_init_hook(env):
    """Register stock_3pl_core capabilities and TPLService on install."""
    from odoo.addons.stock_3pl_core.services.tpl_service import TPLService
    env['mml.capability'].register(
        [
            '3pl.message.queue',
            '3pl.connector.get',
            '3pl.inbound.create',
        ],
        module='stock_3pl_core',
    )
    env['mml.registry'].register('3pl', TPLService)


def uninstall_hook(env):
    """Deregister all stock_3pl_core entries on uninstall."""
    env['mml.capability'].deregister_module('stock_3pl_core')
    env['mml.registry'].deregister('3pl')
    env['mml.event.subscription'].deregister_module('stock_3pl_core')
