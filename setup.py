import setuptools

with open('README.md', 'r') as fh:
    long_description = fh.read()

setuptools.setup(
    name='unittest-asyncio-concurrent',
    version='1',
    author='Versus Void',
    description='Concurrent execution of async unit tests',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/versusvoid/unittest-asyncio-concurrent',
    packages=setuptools.find_namespace_packages(include=['uac.*']),
    classifiers=[
        'Framework :: AsyncIO',
        'Topic :: Software Development :: Testing :: Unit',
    ],
    python_requires='>=3.8',
)
