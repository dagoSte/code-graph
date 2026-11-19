import click

@click.group()
def cli():
    pass

@cli.command()
def sync_all():
    return 1

if __name__ == '__main__':
    cli()
