import setuptools

with open('README.md', 'r') as fh:
    long_description = fh.read()

setuptools.setup(
    name='unittest-asyncio-parallel',
    version='1',
    author='Versus Void',
    description='Utilities',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/versusvoid/unittest-asyncio-parallel',
    packages=setuptools.find_namespace_packages(include=['uap.*']),
    classifiers=[
        'Framework :: AsyncIO',
    ],
    python_requires='>=3.8',
)
