import json
from pathlib import Path
import click

from app.pipeline import run_pipeline


@click.command()
@click.argument('folder', type=click.Path(exists=True))
def main(folder):
    folder_path = Path(folder)
    for txt_file in folder_path.glob('*.txt'):
        text = txt_file.read_text(encoding='utf-8')
        result = run_pipeline(text)
        clean_path = txt_file.with_name(txt_file.stem + '-clean.txt')
        flag_path = txt_file.with_name(txt_file.stem + '-flags.json')
        clean_path.write_text(result['clean_text'], encoding='utf-8')
        flag_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
