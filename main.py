import json
import urllib.parse
from subprocess import check_output
from concurrent.futures import as_completed, ThreadPoolExecutor
import time
import os
import string
import random
import gzip
import subprocess
import requests


def assure_folder_exists(folder, root):
    full_path = os.path.join(root, folder)
    if os.path.isdir(full_path):
        pass
    else:
        os.mkdir(full_path)
    return full_path


def random_filename(length, ext):
    return ''.join([random.choice(string.ascii_lowercase) for _ in range(length)]) + '.{}'.format(ext)


class File:
    def __init__(self, name, link):
        self.name = name
        self.link = link

    def __repr__(self):
        return '<F:{}>'.format(self.name)


class StickerDownloader:
    def __init__(self, token, session=None, multithreading=4):
        self.THREADS = multithreading
        self.token = token
        self.cwd = assure_folder_exists('downloads', root=os.getcwd())
        if session is None:
            self.session = requests.Session()
        else:
            self.session = session
        self.api = 'https://api.telegram.org/bot{}/'.format(self.token)
        verify = self._api_request('getMe', {})
        if verify['ok']:
            pass
        else:
            print('Invalid token.')
            exit()

    def _api_request(self, fstring, params):
        try:
            param_string = '?' + urllib.parse.urlencode(params)
            res = self.session.get('{}{}{}'.format(self.api, fstring, param_string))
            if res.status_code != 200:
                raise Exception
            res = json.loads(res.content.decode('utf-8'))
            if not res['ok']:
                raise Exception(res['description'])
            return res

        except Exception as e:
            print('API method {} failed. Error: "{}"'.format(fstring, e))
            return None

    def get_file(self, file_id):
        info = self._api_request('getFile', {'file_id': file_id})
        f = File(name=info['result']['file_path'].split('/')[-1],
                 link='https://api.telegram.org/file/bot{}/{}'.format(self.token, info['result']['file_path']))

        return f

    def get_sticker_set(self, name):
        params = {'name': name}
        res = self._api_request('getStickerSet', params)
        if res is None:
            return None
        stickers = res['result']['stickers']
        files = []
        print('Starting to scrape "{}" ..'.format(name))
        start = time.time()
        with ThreadPoolExecutor(max_workers=self.THREADS) as executor:
            futures = [executor.submit(self.get_file, i['file_id']) for i in stickers]
            for i in as_completed(futures):
                files.append(i.result())

        end = time.time()
        print('Time taken to scrape {} stickers - {:.3f}s'.format(len(files), end - start))
        print()

        sticker_set = {
            'name': res['result']['name'].lower(),
            'title': res['result']['title'],
            'files': files
        }
        return sticker_set

    def download_file(self, name, link, path):
        file_path = os.path.join(path, name)
        with open(file_path, 'wb') as f:
            res = self.session.get(link)
            f.write(res.content)

        return file_path

    def download_sticker_set(self, sticker_set):
        swd = assure_folder_exists(sticker_set['name'], root=self.cwd)
        download_path = assure_folder_exists('webp', root=swd)
        downloads = []

        print('Starting download of "{}" into {}'.format(sticker_set['name'], download_path))
        start = time.time()
        with ThreadPoolExecutor(max_workers=self.THREADS) as executor:
            futures = [executor.submit(self.download_file, f.name, f.link, download_path) for f in sticker_set['files']]
            for i in as_completed(futures):
                downloads.append(i.result())

        end = time.time()
        print('Time taken to download {} stickers - {:.3f}s'.format(len(downloads), end - start))
        print()

        return downloads

    @staticmethod
    def convert_file(_input, _output):
        if _input.endswith('.webp'):
            command = 'dwebp -quiet "{}" -o "{}"'.format(_input, _output)
            check_output(command, shell=True)
            return _output
        elif _input.endswith('.tgs'):
            try:
                gif_output = _output.replace('.png', '.gif')
                temp_json = _output.replace('.png', '.json')

                with gzip.open(_input, 'rb') as f_in:
                    with open(temp_json, 'wb') as f_out:
                        f_out.write(f_in.read())

                try:
                    subprocess.check_output(
                        f'lottie_convert.py "{temp_json}" "{gif_output}" --output-format gif',
                        shell=True
                    )
                    os.remove(temp_json)
                    return gif_output
                except Exception as gif_err:
                    print(f"Direct GIF conversion failed, trying frame extraction: {gif_err}")

                    temp_dir = _output.replace('.png', '_frames')
                    if not os.path.exists(temp_dir):
                        os.makedirs(temp_dir)

                    subprocess.check_output(
                        f'lottie_convert.py "{temp_json}" "{temp_dir}/frame_%04d.png" --output-format png',
                        shell=True
                    )

                    frames = sorted([f for f in os.listdir(temp_dir) if f.startswith('frame_')])
                    if len(frames) <= 1:
                        print(f"Warning: Only {len(frames)} frames extracted for {os.path.basename(_input)}")

                    subprocess.check_output(
                        # f'magick "{temp_dir}/frame_*.png" -transparent white -dispose background -delay 3 -loop 0 "{gif_output}"',
                        f'magick -background none -alpha set -dispose background "{temp_dir}/frame_*.png" -loop 0 -delay 3 "{gif_output}"',
                        shell=True
                    )

                    os.remove(temp_json)
                    import shutil
                    shutil.rmtree(temp_dir)

                    return gif_output
            except Exception as e:
                print(f"Error converting TGS to GIF: {e}")
                return None
        else:
            print(f"Unknown file format: {os.path.basename(_input)}")
            return None

    def convert_to_images(self, name):
        swd = assure_folder_exists(name, root=self.cwd)
        webp_folder = assure_folder_exists('webp', root=swd)
        output_folder = assure_folder_exists('matrix_stickers', root=swd)

        all_files = [os.path.join(webp_folder, i) for i in os.listdir(webp_folder)]
        converted_files = []
        skipped_files = 0

        print(f'Converting stickers for Matrix "{name}"..')
        start = time.time()

        with ThreadPoolExecutor(max_workers=self.THREADS) as executor:
            futures = []
            for _input in all_files:
                if _input.endswith('.webp'):
                    output_path = os.path.join(output_folder, random_filename(6, 'png'))
                    futures.append(executor.submit(self.convert_file, _input, output_path))
                elif _input.endswith('.tgs'):
                    output_path = os.path.join(output_folder, random_filename(6, 'png'))
                    futures.append(executor.submit(self.convert_file, _input, output_path))
                else:
                    skipped_files += 1
                    print(f"Skipping unknown format: {os.path.basename(_input)}")

            for i in as_completed(futures):
                try:
                    result = i.result()
                    if result:
                        converted_files.append(result)
                except Exception as e:
                    print(f"Error converting file: {e}")

        end = time.time()
        print(f'Time taken to convert {len(converted_files)} stickers - {end-start:.3f}s')
        if skipped_files > 0:
            print(f'Skipped {skipped_files} files (unknown formats)')
        print()

        return converted_files


if __name__ == "__main__":
    print('Welcome to Telegram Downloader..')

    TOKEN = os.getenv('TG_TOKEN')
    if TOKEN is None:
        TOKEN = input("Enter your Telegram Bot Token: ").strip()

    if TOKEN == '':
        print('Invalid token.')
        exit()

    downloader = StickerDownloader(TOKEN)
    names = []
    while True:
        name = input("Enter sticker_set url (leave blank to stop): ").strip()
        if name == '':
            break
        names.append(name.split('/')[-1])

    for sset in names:
        print('=' * 60)
        _ = downloader.get_sticker_set(sset)
        if _ is None:
            continue
        print('-' * 60)
        _ = downloader.download_sticker_set(_)
        print('-' * 60)
        downloader.convert_to_images(sset)
