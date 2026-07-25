# -*- coding: utf-8 -*-
# Provider implementations of the `mail.provider.client` contract. Nothing
# outside this package may build provider URLs, import provider SDKs, or reason
# about provider-specific payload shapes.
from . import microsoft
from . import google
