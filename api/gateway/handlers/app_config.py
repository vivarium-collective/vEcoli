import json

from api.data_model.gateway import RouterConfig



def get_config(path: str):
    with open(path, 'r') as f:
        return json.load(f)
    

def find_router(api_name: str, router_configs: list[RouterConfig]):
    finder = filter(
        lambda router: router.id == api_name,
        router_configs
    )
    try:
        return next(finder)
    except StopIteration:
        return None